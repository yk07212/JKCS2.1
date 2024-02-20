#!/usr/bin/env python3
'''Dynamic Approach for Transition States'''
###############################LIBRARIES#####################################
import numpy as np
import pandas as pd
import argparse
import os
import re
import time
import threading
import copy
# import shutil
from classes import Molecule, Logger
from slurm_submit import submit_array_job, submit_job, update_molecules_status

global_molecules = []
convergence_errors = ["l9999", "l508"]
intervention_errors = ["l301"]
np.set_printoptions(suppress=True, precision=6)
###############################################################################

class SCFAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        # Determine the algorithm based on the option_string
        algorithm = option_string.strip('-').upper()
        # Default SCF string when no additional arguments are provided
        scf_string = f"SCF=({algorithm})"
        # Update the SCF string if additional arguments are provided
        if values is not None:
            # Join additional arguments with a comma
            additional_args = ', '.join(values)
            if additional_args:
                scf_string = f"SCF=({algorithm}, maxcycle={additional_args})"
            else:
                scf_string = f"SCF={algorithm}"
        setattr(namespace, self.dest, scf_string)

class ParseCHO(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if isinstance(values, list):
            flat_list = [item for sublist in values for item in (sublist.split(',') if ',' in sublist else [sublist])]
            int_list = [int(item) for item in flat_list]
        else:
            int_list = [int(item) for item in values.split(',')]
        
        setattr(namespace, self.dest, int_list)


def str2bool(v):
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def mkdir(molecule, crest_constrain=True):
    if not os.path.exists(molecule.directory):
        os.makedirs(molecule.directory, exist_ok=True)
        if not os.path.exists(os.path.join(molecule.directory, "input_files")):
            os.makedirs(os.path.join(os.path.join(molecule.directory, "input_files")))
        if not os.path.exists(os.path.join(molecule.directory, "log_files")):
            os.makedirs(os.path.join(os.path.join(molecule.directory, "log_files")))
    if crest_constrain:
        crest_constrain_file(molecule)


def pkl_to_xyz(pkl_file_path, max_conformers=50):
    if args.max_conformers:
        max_conformers = args.max_conformers
    all_conformers = []
    with open(pkl_file_path, 'rb') as f:
        df = pd.read_pickle(f) 
        for xyz in df[('xyz', 'structure')]:
            coordinates_list = []
            np.set_printoptions(precision=6, suppress=True)
            coords = xyz.get_positions()
            atom = xyz.get_chemical_symbols()
            for atom, coord in zip(atom, coords):
                coordinates = [atom, coord[0], coord[1], coord[2]]
                coordinates_list.append(coordinates)
            all_conformers.append(coordinates_list)
    return all_conformers[:max_conformers]


def extract_normal_coordinates(molecule):
    '''Return format: [[0.02, -0.02, 0.06], [0.07, -0.11, -0.02], ...]''' 
    with open(molecule.log_file_path, 'r') as file:
        lines = file.readlines()
    negative_freq_found = False
    xyz_coordinates = []
    for line in lines:
        if "Frequencies --" in line and '-' in line:
            negative_freq_found = True
            continue
        if any(substring in line for substring in ['Red. masses', 'Frc consts', 'IR Inten', 'Atom  AN']):
            continue
        if negative_freq_found:
            if re.match(r'\s*\d+\s+\d+\s+(-?\d+\.\d+\s+)*', line):
                xyz = re.findall(r'-?\d+\.\d+', line)[:3]  # Extract first three floats
                xyz = [float(coord) for coord in xyz]
                xyz_coordinates.append(xyz)
            else:
                break

    return xyz_coordinates[:-1]


def bad_geometry(molecule):
    C_index = molecule.constrained_indexes['C']-1
    H_index = molecule.constrained_indexes['H']-1
    O_index = molecule.constrained_indexes['O']-1

    distance_CH = molecule.atom_distance(molecule.coordinates[C_index], molecule.coordinates[H_index])
    distance_HO = molecule.atom_distance(molecule.coordinates[H_index], molecule.coordinates[O_index])
    angle_CHO = molecule.calculate_angle(molecule.coordinates[C_index], molecule.coordinates[H_index], molecule.coordinates[O_index])
    if distance_CH < 1.1 or distance_HO > 1.40 and angle_CHO < 170.0:
        return True
    return False


def check_transition_state(molecule, logger):
    if args.freq_cutoff:
        freq_cutoff = -abs(args.freq_cutoff)
    else:
        freq_cutoff = -100
   
    logger.log(f"Checking transition state of {molecule.name}")
    sorted_negative_freqs = sorted((freq for freq in molecule.vibrational_frequencies if freq < 0), key=abs)
    if sorted_negative_freqs:
        imag = sorted_negative_freqs[0]
        if imag < freq_cutoff:
            logger.log("Imaginary frequency found under cutoff. Proceeding with check of normal coordinates of transition state")
        elif freq_cutoff < imag < 0:
            logger.log(f"Small negative frequency between cutoff {freq_cutoff} and 0: {imag} for molecule {molecule.name}")
            logger.log(f"This may indicate wrong transition state. Checking geometry")
            if bad_geometry(molecule):
                print("BAD!", molecule.name)
                logger.log("Unusual bond lengths and angles detected. Trying to correct")
                molecule.set_active_site(indexes=args.CHO)
                return False
            else:
                logger.log("No unusual bond angles or lengths detected. Checking normal coordinates of transition state")
    else:
        logger.log("No negative frequency found for transition state")
        logger.log("Trying to correct geometry and submit for TS calculation")
        molecule.set_active_site(indexes=args.CHO)  # testing this in pinic_acid/bad_TS_test
        return False

    normal_coords = extract_normal_coordinates(molecule)
    
    if len(normal_coords) != len(molecule.coordinates):
        print("Error: The number of normal mode displacements does not match the number of atoms in the molecule.")
        return 

    # Copy original coordinates and apply displacement
    displaced_coordinates_plus = [np.array(original) + np.array(displacement) for original, displacement in zip(molecule.coordinates, normal_coords)]
    displaced_coordinates_minus = [np.array(original) - np.array(displacement) for original, displacement in zip(molecule.coordinates, normal_coords)]

    H_index = molecule.constrained_indexes['H']
    O_index = molecule.constrained_indexes['O']

    # Calculate original and new distances between H and O
    original_distance_HO = np.linalg.norm(np.array(molecule.coordinates[H_index]) - np.array(molecule.coordinates[O_index]))
    new_distance_HO_plus = np.linalg.norm(displaced_coordinates_plus[H_index] - displaced_coordinates_plus[O_index])
    new_distance_HO_minus = np.linalg.norm(displaced_coordinates_minus[H_index] - displaced_coordinates_minus[O_index])

    if new_distance_HO_plus < original_distance_HO and new_distance_HO_minus > original_distance_HO or new_distance_HO_plus > original_distance_HO and new_distance_HO_minus < original_distance_HO:
        logger.log_with_stars(f"Yay {molecule.name} has converged with imaginary frequency: {imag}")
        return True
    else:
        if imag < freq_cutoff:
            logger.log(f"Change in bond length does not meet threshold. However, magnitude of imaginiary frequency fulfills criteria. Passing {molecule.name} for now, but check geometry")
            return True
        else:
            logger.log("The change in bond length does not meet the threshold for significant shortening or elongation and neither did the magnitude of imaginary frequency.")
            return False


def ArbAlign_compare_molecules(molecules, logger, RMSD_threshold=0.38):
    import ArbAlign
    filtered_molecules = ArbAlign.main(molecules, RMSD_threshold)
    Molecule.molecules_to_pickle(filtered_molecules, os.path.join(start_dir, "filtered_molecules.pkl"))
    logger.log(f"Filtered {len(molecules)} to {len(filtered_molecules)}")

    return filtered_molecules


def crest_constrain_file(molecule, force_constant=1.00):
    '''Force constant: How tight to constrain the atoms i.e. the magnitude of the oscillation between the constrained atoms'''
    C_index = molecule.constrained_indexes['C']
    H_index = molecule.constrained_indexes['H']
    O_index = molecule.constrained_indexes['O']
    with open (molecule.directory + "/constrain.inp","w") as f:
        f.write("$constrain\n")
        f.write(f"  force constant={force_constant}\n") 
        f.write(f"  distance: {C_index}, {H_index}, auto\n")
        f.write(f"  distance: {H_index}, {O_index}, auto\n")
        # f.write(f"  angle: {C_index}, {H_index}, {O_index}, auto\n")
        f.write("$end\n")

    with open (molecule.directory + "/.constrain","w") as f:
        f.write(f"C: {C_index}\n")
        f.write(f"H: {H_index}\n")
        f.write(f"O: {O_index}\n")


def QC_input(molecule, constrain,  method, basis_set, TS, C_index=None, H_index=None, O_index=None):
    file_name = f"{molecule.name}{molecule.input}"
    file_path = os.path.join(molecule.directory, file_name)
    atoms = molecule.atoms
    coords = molecule.coordinates
    max_iter = 150 # Maximum iterations for geoemtry optimization
    freq = 'freq'
    SCF = 'NoTrah'


    # Ensure correct usage of unretricted method for Gaussian16
    if molecule.program.lower() == 'g16':
        if molecule.mult == 2 and method[0].lower() != "u":
            method = f"u{method}"
        elif molecule.mult == 1 and method[0].lower() == "u":
            method = method[1:]

    if constrain:
        if not molecule.constrained_indexes:
            molecule.find_active_site(indexes=args.CHO)
        if molecule.program.lower() == 'orca': # ORCA indexes from 0
            C_index = molecule.constrained_indexes['C']-1
            H_index = molecule.constrained_indexes['H']-1
            O_index = molecule.constrained_indexes['O']-1
        else:
            C_index = molecule.constrained_indexes['C']
            H_index = molecule.constrained_indexes['H']
            O_index = molecule.constrained_indexes['O']

    if molecule.program.lower() == "orca" and molecule.converged is False:
        with open(file_path, "w") as f:
            if method == "DLPNO":
                # Consider scaling of the memory with the molecule size
                if molecule.error_termination_count == 1:
                    f.write(f"! aug-cc-pVTZ aug-cc-pVTZ/C DLPNO-CCSD(T) VeryTightSCF RI-JK aug-cc-pVTZ/JK {SCF}\n")
                    # f.write(f"! cc-pVTZ-F12 cc-pVTZ/C cc-pVTZ-F12-CABS DLPNO-CCSD(T)-F12 TightPNO TightSCF\n")
                    f.write(f"%pal nprocs {args.cpu} end\n")
                    f.write(f"%maxcore {round((args.mem+16000)/args.cpu)}\n") 
                elif molecule.error_termination_count == 2:
                    f.write(f"! aug-cc-pVTZ aug-cc-pVTZ/C DLPNO-CCSD(T) VeryTightSCF RI-JK aug-cc-pVTZ/JK {SCF}\n")
                    f.write(f"%pal nprocs {args.cpu} end\n")
                    f.write(f"%maxcore {round((args.mem+20000)/args.cpu)}\n") 
                else:
                    f.write(f"! aug-cc-pVTZ aug-cc-pVTZ/C DLPNO-CCSD(T) TightSCF RI-JK aug-cc-pVTZ/JK {SCF}\n")
                    f.write(f"%pal nprocs {args.cpu} end\n")
                    f.write(f"%maxcore {round((args.mem+12000)/args.cpu)}\n") 
            elif TS:
                f.write(f"! {method} {basis_set} TightSCF SlowConv OptTS {freq}\n")
                f.write(f"%pal nprocs {args.cpu} end\n")
                f.write(f"%maxcore {round(args.mem/args.cpu)}\n")
            else:
                f.write(f"! {method} {basis_set} TightSCF SlowConv OPT {freq}\n")
                f.write(f"%pal nprocs {args.cpu} end\n")
                f.write(f"%maxcore {round(args.mem/args.cpu)}\n")
            if constrain:
                f.write("%geom\n")
                f.write("Constraints\n")
                f.write(f"{{B {C_index} {H_index} C}}\n") # Bond being broken
                f.write(f"{{B {H_index} {O_index} C}}\n") # Bond being formed
                # f.write(f"{{A {C_index-1} {H_index-1} {O_index-1} C}}\n") # CHO angle
                f.write("end\nend\n")
            elif TS:
                f.write("%geom\n")
                f.write(f"maxiter {max_iter}\n")
                f.write("Calc_Hess true\n")
                if molecule.error_termination_count == 1:
                    f.write("Recalc_Hess 5\n")
                    f.write("MaxStep 0.1\n") # Maybe 0.2?
                elif molecule.error_termination_count == 2:
                    f.write("Recalc_Hess 1\n")
                    f.write("MaxStep 0.1\n")
                else:
                    f.write("Recalc_Hess 10\n")
                # f.write(f"TS_Mode {{ B {C_index} {H_index} }} end\n")
                f.write(f"TS_Active_Atoms {{ {C_index} {H_index} {O_index} }} end\n")
                f.write("TS_Active_Atoms_Factor 3\n")
                f.write("end\n")
            f.write("\n")
            f.write(f"* xyz {molecule.charge} {molecule.mult}\n") 
            for atom, coord in zip(atoms, coords):
                f.write(f'{atom} {coord[0]:.6f} {coord[1]:.6f} {coord[2]:.6f}\n')
            f.write("*")

########################################################G16###############################################################3
    elif molecule.program.lower() == "g16" and molecule.converged is False:
        with open(file_path, "w") as f:
            f.write(f"%nprocshared={args.cpu}\n")
            f.write(f"%mem={args.mem}mb\n")
            if TS and constrain: # should only be in the case of hard convergence problems where some flexible parts should be constrained.
                f.write(f'# {method} {basis_set} opt=(calcfc,ts,noeigen,modredundant,MaxCycles={max_iter}) freq {args.SCF}\n\n')
            elif TS and constrain is False:
                if molecule.error_termination_count == 1:
                    f.write(f'# {method} {basis_set} opt=(calcfc,ts,noeigen,MaxCycles={max_iter},ReCalcFC=5,Maxstep=10) freq {args.SCF}\n\n') # RecalcFC=N also option, recalc Hessian every N iteration
                elif molecule.error_termination_count == 2:
                    f.write(f'# {method} {basis_set} opt=(calcfc,ts,noeigen,ReCalcFC=2,MaxCycles={max_iter},MaxStep=10) freq {args.SCF}\n\n')
                # elif molecule.error_termination_count == 3:
                #     molecule.set_active_site(perturb=True)
                #     f.write(f'# {method} {basis_set} opt=(calcfc,ts,noeigen,CalcAll,MaxCycles={max_iter}) freq {args.SCF}\n\n')
                else:
                    f.write(f'# {method} {basis_set} opt=(calcfc,ts,noeigen,MaxCycles={max_iter},RecalcFC=10) freq {args.SCF}\n\n')
            elif TS is False and constrain:
                f.write(f'# {method} {basis_set} opt=modredundant {args.SCF}\n\n')
            else:
                f.write(f"# {method} {basis_set} opt {freq} {args.SCF}\n\n")
            f.write("Title\n\n")
            f.write(f"{molecule.charge} {molecule.mult}\n")
            for atom, coord in zip(atoms, coords):
                f.write(f'{atom} {coord[0]:.6f} {coord[1]:.6f} {coord[2]:.6f}\n')
            f.write("\n")
            if constrain:
                f.write(f"B {C_index} {H_index} F\n")
                f.write(f"B {H_index} {O_index} F\n")
                # f.write(f"A {C_index} {H_index} {O_index} F\n") # CHO angle
                f.write("\n")
    else:
        print(f"QC_input was called but no program was specified for {molecule.name}")


def NEP_input(file_path, file_name):
    if args.NEB:
        with open(file_path + "/NEB_TS.inp", "w") as f:
            f.write(f'! B3LYP 6-31+g(d,p)  NEB-TS FREQ\n')
            f.write(f'%NEB PREOPT_ENDS TRUE NEB_END_XYZFILE "{file_path + "/" + file_name}_product.xyz" END\n')
            f.write(f'* XYZfile 0 2 {file_path + "/" + file_name}_reactant.xyz\n')


def energy_cutoff(molecules, logger, initial_cutoff=5, max_cutoff_increment=30.0, increment_step=5.0):
    """
    Filter molecules to those within a certain energy range of the lowest energy conformer.
    Adjusts cutoff to avoid removing more than 50% of molecules.

    molecules: List of molecule objects.
    initial_cutoff: Initial energy cutoff in kcal/mol (default is 5 kcal/mol).
    max_cutoff_increment: Maximum additional cutoff to add (default is 10 kcal/mol).
    increment_step: Step size for increasing the cutoff (default is 1 kcal/mol).
    """
    if args.energy_cutoff:
        initial_cutoff = args.energy_cutoff
    hartree_to_kcalmol = 627.509

    # Convert to kcal/mol from Hartree and sort by energy
    energies = [molecule.zero_point_corrected * hartree_to_kcalmol for molecule in molecules]
    min_energy = min(energies)

    cutoff = initial_cutoff
    while cutoff <= initial_cutoff + max_cutoff_increment:
        # Filter molecules within the current cutoff range
        filtered_molecules = [molecule for molecule, energy in zip(molecules, energies)
                              if (energy - min_energy) <= cutoff]

        # Check if more than 50% of molecules are retained
        if len(filtered_molecules) >= 0.5 * len(molecules):
            return filtered_molecules

        # Increase cutoff
        cutoff += increment_step

    # Return filtered list if cutoff reaches the maximum limit
    return filtered_molecules


def read_last_lines(filename, logger, num_lines, interval=200):
    attempts = 0
    max_attempts = 10

    while attempts < max_attempts:
        try:
            with open(filename, 'rb') as f:
                f.seek(0, os.SEEK_END)
                buffer_size = 8192
                content = ''
                while len(content.splitlines()) < num_lines + 1:
                    byte_offset = f.tell() - buffer_size
                    if byte_offset < 0: 
                        byte_offset = 0
                    f.seek(byte_offset)
                    content = f.read().decode('utf-8')
                    if f.tell() == 0: 
                        break  # Reached the beginning of the file
                return content.splitlines()[-num_lines:]
        except FileNotFoundError:
            attempts += 1
            # logger.log(f"Attempt {attempts}: File {filename} not found. Sleeping for {interval} seconds before retrying.")
            time.sleep(interval)

    logger.log(f"Failed to find the file {filename} after {max_attempts} attempts.")
    return False


def resubmit_job(molecule, logger, error=None):
    molecule.move_failed()
    job_type = molecule.current_step
    if job_type == 'opt_constrain':
        QC_input(molecule, constrain=True, method=high_method, basis_set=high_basis, TS=False)

    elif job_type == 'TS_opt':
        QC_input(molecule, constrain=False,  method=high_method, basis_set=high_basis, TS=True)

    elif job_type == 'DLPNO':
        molecule.program = 'ORCA'
        QC_input(molecule, constrain=False, method="DLPNO", basis_set=high_basis, TS=False)

    elif job_type == 'optimization':
        QC_input(molecule, constrain=False, method=high_method, basis_set=high_basis, TS=False)

    job_id, _ = submit_job(molecule, args)
    molecule.job_id = f"{job_id}"
    logger.log(f"submitted file {molecule.name}{molecule.input} with job type: {job_type} and job id {molecule.job_id} in directory {molecule.directory}")


def check_convergence(molecules, logger, threads, interval, max_attempts):
    initial_delay = args.initial_delay if args.initial_delay else int(interval * 3)
    interval = args.interval if args.interval else int(interval)
    attempts = 0
    sleeping = 0
    pending, running = [], []
    job_type = molecules[0].current_step

    termination = termination_strings.get(molecules[0].program.lower(), "")
    error_termination = error_strings.get(molecules[0].program.lower(), "")

    all_converged = False
    for m in molecules:  # Initialize with all molecules not being converged and no terminations counted
        m.converged = False
        m.error_termination_count = 0

    logger.log(f"Waiting {initial_delay} seconds before first check")
    time.sleep(initial_delay)

    while attempts < max_attempts and not all_converged:
        i = 0
        while i < len(molecules):
            molecule = molecules[i]
            if molecule.error_termination_count >= 3:
                logger.log(f"Molecule {molecule.name} is being dropped due to repeated error terminations.")
                molecules.pop(i)
                # Check if all remaining molecules are converged
                if all(m.converged for m in molecules):
                    all_converged = True
                    break
            else:
                i += 1
        if all_converged:
            break

        update_molecules_status(molecules)
        for molecule in molecules:
            if molecule.converged:
                continue
            if molecule.status == 'pending':
                if molecule.job_id not in pending:
                    logger.log(f"Job {molecule.job_id} is pending in the queue.")
                    pending.append(molecule.job_id)
                continue

            elif molecule.status in ['running', 'completed or not found'] or not molecule.converged:
                if molecule.job_id not in running and molecule.status == 'running':
                    logger.log(f"Job {job_type} for {molecule.name} with job id {molecule.job_id} is running.")
                    running.append(molecule.job_id)
                if molecule.job_id in pending:
                    pending.remove(molecule.job_id)

                log_file_name = f"{molecule.name}{molecule.output}"
                log_file_path = os.path.join(molecule.directory, log_file_name)
                molecule.log_file_path = log_file_path
                last_lines = read_last_lines(log_file_path, logger, 30)
                if not last_lines:
                    molecule.error_termination_count += 1
                    continue

                termination_detected = any(termination in line for line in last_lines)
                error_termination_detected = any(error_termination in line for line in last_lines)

                if termination_detected:
                    logger.log(f"Normal termination detected in {log_file_name}")
                    xyz_coordinates = molecule.log2xyz()
                    molecule.coordinates = xyz_coordinates
                    molecule.update_energy(logger)
                    if 'TS_opt' in job_type:
                        transition_state = check_transition_state(molecule, logger)
                        if transition_state is True:
                            molecule.converged = True
                            molecule.error_termination_count = 0
                            molecule.move_inputfile()
                        else:
                            molecule.error_termination_count += 1
                            if molecule.error_termination_count >= 3:
                                continue
                            resubmit_job(molecule, logger)
                            time.sleep(30)
                            continue
                    elif job_type == 'crest_sampling':
                        crest_conformers = pkl_to_xyz(os.path.join(molecule.directory, f"collection{molecule.name}.pkl"))
                        if crest_conformers:
                            molecule.converged = True
                            molecules = initiate_conformers(crest_conformers, molecule=molecule)
                            all_converged = True
                            break
                    elif job_type == 'DLPNO':
                        molecule.converged = True
                        molecule.update_energy(logger, DLPNO=True)
                        molecule.update_step()
                        logger.log(f"Yay! DLPNO single point for {molecule.name} molecule converged")
                        molecule.move_inputfile()
                    else:
                        molecule.converged = True
                        logger.log(f"**{molecule.name} converged**")
                        xyz_coordinates = molecule.log2xyz()
                        molecule.coordinates = xyz_coordinates
                        molecule.move_inputfile()
                elif error_termination_detected:
                    molecule.error_termination_count += 1
                    handle_error_termination(molecule, logger, last_lines)
                    continue
            else:
                continue
            time.sleep(10)
                
        if all(m.converged for m in molecules):
            all_converged = True
            break

        if len(pending) == len(molecules): # should maybe be majority instead of all
            sleeping = 1
            if sleeping:
                logger.log(f"All the submitted jobs are pending. Sleeping for now.")
            time.sleep(5*interval)
        else:
            attempts += 1
            if attempts % 10 == 0 or attempts == 1:
                logger.log(f"Log files of the {len(molecules)} conformers have been checked. Checking every {interval} seconds. Attempt: {attempts}/{max_attempts}")
            time.sleep(interval)

    if all_converged:
        if molecules: # Check if not all molecules from list has been dropped
            dir = molecules[0].directory
            basename = os.path.basename(dir)
            pickle_path = os.path.join(dir, f'{basename}_{job_type}.pkl')
            molecules[0].move_files()
            if len(molecules) > 1:
                Molecule.molecules_to_pickle(molecules, pickle_path)
                logger.log_with_stars(f"Yay! All conformer jobs have converged for job type: {job_type}.")
            else:
                logger.log("Proceeding with next step.")
            if job_type == "DLPNO":
                if molecules[0].product and any(mol for mol in molecules if 'H2O' in mol.name) is False:
                    H2O = Molecule.create_H2O()
                    QC_input(H2O, constrain=False, method=high_method, basis_set=high_basis, TS=False)
                    submit_and_monitor(H2O, logger, threads)
                elif molecules[0].reactant and any(mol for mol in molecules if 'OH' in mol.name) is False:
                    OH = Molecule.create_OH()
                    QC_input(OH, constrain=False, method=high_method, basis_set=high_basis, TS=False)
                    submit_and_monitor(OH, logger, threads)
                for molecule in molecules: 
                    global_molecules.append(molecule)
                return True
            elif args.auto:
                handle_termination(molecules, logger, threads, converged=True)
                return True
            else:
                logger.log(f"Automatic processing of calculations has been turned off and JKTS will not proceed with next step: {molecules[0].next_step}")
                logger.log(f"A pickle file {basename}_{job_type}.pkl has been created. Next step {molecules[0].next_step} for molecules can be started from this.")
                return True
        else:
            logger.log(f"No conformer has managed to converge for job type: {job_type}")
            return False


def handle_error_termination(molecule, logger, last_lines):
    convergence_errors = ["l9999", "l508"]
    intervention_errors = ["l301"]
    G16_common_errors = "https://wongzit.github.io/gaussian-common-errors-and-solutions/"

    detected_convergence_errors = [error for error in convergence_errors if any(error in line for line in last_lines)]
    detected_intervention_errors = [error for error in intervention_errors if any(error in line for line in last_lines)]

    if detected_convergence_errors:
        error = detected_convergence_errors[0]
        logger.log(f"Convergence error '{error}' termination found in {molecule.name}")
        xyz_coordinates = molecule.log2xyz()
        molecule.coordinates = xyz_coordinates
        logger.log(f"Trying to resubmit job for {molecule.name} due to error termination")
        resubmit_job(molecule, logger, error)
    elif detected_intervention_errors:
        error = detected_intervention_errors[0]
        logger.log(f"Error '{error}' detected in {molecule.name} which needs taken care of manually")
        logger.log(f"Removing the conformer {molecule.name} for now so user can inspec error")
        if molecule.program.lower() == 'g16':
            logger.log(f"Common G16 error are listed in: {G16_common_errors}")
        # molecule.set_active_site()
    else:
        logger.log(f"Error termination found in {molecule.name}. Trying to resubmit")
        xyz_coordinates = molecule.log2xyz()
        molecule.coordinates = xyz_coordinates
        resubmit_job(molecule, logger)


def check_crest_products(molecules, logger, threads, interval, max_attempts):
    if args.initial_delay:
        initial_delay = args.initial_delay
    else:
        initial_delay = int(interval*3)
    if args.interval:
        interval = args.interval
    else:
        interval = int(interval)

    attempts = 0
    sleeping = 0
    pending = []
    all_conformers = []
    expected_files = {f"collection{molecule.name}.pkl" for molecule in molecules}
    
    logger.log(f"Waiting {initial_delay} seconds before first check")
    time.sleep(initial_delay)

    while attempts < max_attempts:
        update_molecules_status(molecules)
        for molecule in molecules:
            if molecule.status == 'pending':
                if molecule.job_id not in pending:
                    logger.log(f"Job {molecule.job_id} is pending in the queue.")
                    pending.append(molecule.job_id)
                continue
        if len(pending) == len(molecules):
            sleeping = 1
            if sleeping:
                logger.log("All jobs are pending. Sleeping for now")
            time.sleep(5*interval)
            continue

        try:
            files_in_directory = set(os.listdir(molecules[0].directory))
        except FileNotFoundError:
            logger.log(f"Pickle files not generated yet. Retrying in {interval} seconds.")
            time.sleep(interval)
            attempts += 1
            continue

        if expected_files.issubset(files_in_directory):
            for molecule in molecules:
                try:
                    conformers = pkl_to_xyz(os.path.join(molecule.directory, f"collection{molecule.name}.pkl"))
                    for n, conformer_coords in enumerate(conformers, start=1):
                        conformer_molecule = copy.deepcopy(molecule)
                        conformer_molecule.name = f"{molecule.name}_conf{n}"
                        conformer_molecule.mult = 2
                        conformer_molecule.product = True
                        conformer_molecule.set_current_step('crest_sampling')
                        conformer_molecule.atoms = [atom[0] for atom in conformer_coords]
                        conformer_molecule.coordinates = [atom[1:] for atom in conformer_coords]
                        all_conformers.append(conformer_molecule)
                except Exception as e:
                    logger.log(f"Error processing molecule {molecule.name}: {e}")
                    return False
            logger.log(f"CREST conformers for all products are generated. Proceeding with next step: {all_conformers[0].next_step}")
            handle_termination(all_conformers, logger, threads, converged=True)
            return True
        else:
            logger.log(f"Not all files found. Retrying in {interval} seconds.")
            time.sleep(interval)

        attempts += 1

    return False


def submit_and_monitor(molecules, logger, threads):
    if isinstance(molecules, Molecule):
        molecules = [molecules]

    if len(molecules) == 1:
        job_id, interval = submit_job(molecules[0], args)
        logger.log(f"Submitting file {molecules[0].name}{molecules[0].input} for calculation in path {molecules[0].directory} with job id {job_id}")
    else:
        job_id, interval = submit_array_job(molecules, args)
        logger.log(f"Submitted SLURM array job with job id {job_id} for conformers in {molecules[0].directory}")

    if job_id is not None:
        if molecules[0].current_step == 'crest_sampling' and molecules[0].product:
            thread = threading.Thread(target=check_crest_products, args=(molecules, logger, threads, interval, args.attempts))
        else:
            thread = threading.Thread(target=check_convergence, args=(molecules, logger, threads, interval, args.attempts))
        threads.append(thread)
        thread.start()
    else: logger.log("Error getting job id")


def handle_termination(molecules, logger, threads, converged):
    if not isinstance(molecules, list):
        try:
            molecules = list(molecules)
        except TypeError:
            raise ValueError("molecules must be a list or convertible to a list")
    if converged:
        for m in molecules: 
            m.converged = False
            m.update_step()
    current_step = molecules[0].current_step
    logger.log(f"Job to be performed: {current_step}")
    if current_step == args.filter_step and converged:
        if molecules[0].product: # For products ArbAlign is needed to be done on every individual H
            conformer_molecules = []
            h_numbers = sorted(set(m.name.split('_H')[1][0] for m in molecules if "_H" in m.name))
            grouped_lists = [[m for m in molecules if f"_H{h_num}_" in m.name] for h_num in h_numbers]
            for h, group in zip(h_numbers, grouped_lists):
                if len(group) > 5:
                    logger.log(f"Filtering product molecules for H{h} using ArbAlign alogrithm")
                    filtered_group = ArbAlign_compare_molecules(group, logger)
                    for molecule in filtered_group:
                        conformer_molecules.append(molecule)
                else:
                    logger.log(f"Skipping filtering for H{h} as the number of conformers is less than 5")
                    for molecule in group:
                        conformer_molecules.append(molecule)
        else:
            if len(molecules) > 5:
                logger.log("Filtering molecules using ArbAlign algorithm")
                conformer_molecules = ArbAlign_compare_molecules(molecules, logger)
            else:
                conformer_molecules = molecules
                logger.log("Skipping filtering as list of molecules is too short")
    else:
        conformer_molecules = molecules

    for conf in conformer_molecules:
        if conf.converged is False:
            conf.program = global_program
            conf.name = conf.name.replace("_TS", "").replace("_CREST", "").replace("_DLPNO", "")
            job_type = conformer_molecules[0].current_step

            if job_type == 'crest_sampling':
                conf.name += '_CREST'
                conf.program = 'CREST'
                output_file_path = os.path.join(conf.directory, f"{conf.name}.xyz")
                conf.write_xyz_file(output_file_path)
            elif job_type in ['opt_constrain', 'opt_constrain_conf']:
                QC_input(conf, constrain=True, method=low_method, basis_set=low_basis, TS=False)

            elif job_type in ['optimization', 'optimization_conf']:
                QC_input(conf, constrain=False, method=high_method, basis_set=high_basis, TS=False)
            
            elif job_type in ['TS_opt', 'TS_opt_conf']:
                conf.name += '_TS'
                QC_input(conf, constrain=False, method=high_method, basis_set=high_basis, TS=True)

            elif job_type == 'DLPNO':
                conf.program = 'ORCA'
                conf.name += '_DLPNO'
                QC_input(conf, constrain=False, method='DLPNO', basis_set=high_basis, TS=False)

            elif job_type  == 'Done':
                logger.log("DLPNO calculation has converged")
                break

            else:
                logger.log("Job type could not be determined for conformer list")
                break
    if conformer_molecules: 
        submit_and_monitor(conformer_molecules, logger, threads)


def termination_status(molecule, logger):
    termination = termination_strings.get(molecule.program, "")
    count = 0
    if 'TS' in molecule.current_step and molecule.program.lower() == 'g16':
        required_count = 2
    else: required_count = 1
    with open(molecule.log_file_path, 'r') as f:
        for line in f:
            if termination in line:
                count += 1
                if count >= required_count:
                    if 'TS' in molecule.current_step:
                        if check_transition_state(molecule, logger) is True:
                            return True
                        else: return False
                    else: return True
        return False


def initiate_conformers(conformers, input_file=None, molecule=None):
    conformer_molecules = []
    if molecule:
        pickle_path = pkl_to_xyz(os.path.join(molecule.directory, f"collection{molecule.name}.pkl"))
        if pickle_path:
            for n, conformer_coords in enumerate(pickle_path, start=1):
                conformer_molecule = copy.deepcopy(molecule)
                conformer_molecule.name = f"{molecule.name}_conf{n}"
                conformer_molecule.atoms = [atom[0] for atom in conformer_coords]
                conformer_molecule.coordinates = [atom[1:] for atom in conformer_coords]
                conformer_molecule.program = 'CREST'
                conformer_molecules.append(conformer_molecule)
        else:
            print(f"Error generating conformers from collection{molecule.name}.pkl")

    elif input_file:
        for n, conformer_coords in enumerate(conformers, start=1):
            name = input_file.split(".")[0].replace("collection", "")
            name += f"_conf{n}"
            conformer_molecule = Molecule(name=name,
            directory=start_dir,
            atoms=[atom[0] for atom in conformer_coords],
            coordinates=[atom[1:] for atom in conformer_coords],
            program='crest')
            log_file_path = os.path.join(start_dir, f"{name}.log")
            if os.path.exists(log_file_path):
                conformer_molecule.log_file_path = log_file_path
            if 'reactant' in input_file:
                conformer_molecule.reactant = True
                conformer_molecule.mult = 1
            elif 'product' in input_file:
                conformer_molecule.product = True
            else:
                conformer_molecule.find_active_site(indexes=args.CHO)
            conformer_molecules.append(conformer_molecule)

    return conformer_molecules


def collect_DFT_and_DLPNO(molecules):
    collected_molecules = []
    
    for m in molecules:
        process_conditions = (m.current_step == 'optimization' and (m.reactant or m.product)) or ('TS_opt' in m.current_step)
        
        if process_conditions:
            if 'OH' in m.name or 'H2O' in m.name:
                identifier = 'OH' if 'OH' in m.name else 'H2O'
            else:
                match = re.search(r'conf(\d+)', m.name)
                identifier = match.group() if match else None

            if identifier:
                for m_DLPNO in molecules:
                    if identifier in m_DLPNO.name and m_DLPNO.current_step == 'DLPNO':
                        m.single_point = m_DLPNO.single_point
                        m.update_step()
                        m.name = m.name.replace('TS', 'DLPNO')
                        collected_molecules.append(m)
                        break

    return collected_molecules


def handle_input_molecules(molecules, logger, threads):
    current_step = molecules[0].current_step
    if all(m.current_step == current_step for m in molecules):
        logger.log(f"Detected molecules with current step: {current_step}")
        logger.log("Checking convergence status of molecules")
        for m in molecules:
            m.directory = start_dir
            if os.path.exists(m.log_file_path):
                converge_status = termination_status(m, logger)
                if current_step == 'Done' and converge_status is True:
                    global_molecules.append(m)
                elif converge_status is True:
                    m.converged = True
                else:
                    m.converged = False
            else: # in the case a single .pkl file has been given as input, and logfiles are not located in same directory:
                if len(molecules) == 1:
                    m.converged = False
                # Check if the conformers in the pkl file have atoms and coordinates and send for calculation.
                elif m.atoms and m.coordinates:
                    m.converged = True
        if all(m.converged for m in molecules):
            logger.log(f"All molecules converged for step {current_step}. Proceeding with next step: {molecules[0].next_step}")
            handle_termination(molecules, logger, threads, converged=True)
        else:
            logger.log(f"Not all molecules given have converged. Non-converged will be calculated and converged ones will be skipped.")
            handle_termination(molecules, logger, threads, converged=False)
    else:
        logger.log(f"Not all molecules in the given files are of the same type. Resubmitting from the last common step.")
            # Implement way to find last common step and also log it so user can see


def log2vib(molecule):
    with open(molecule.log_file_path, 'r') as file:
        content = file.read()
        if molecule.program.lower() == "g16":
            vibrations = re.findall(r"Frequencies --\s+(-?\d+\.\d+)", content)
        elif molecule.program.lower() == "orca":
            vibrations = []
            vib = re.search(r'[-+]?\d*\.\d+\s*cm\*\*-1', content)
            if vib:
                vibration = float(vib.group().split()[0])
                vibrations.append(vibration)
        else:
            return 'No vibrations found'
    return vibrations
        
    
def Gcalc(Emin,GSize,V,A,B,L,h,kB,m,T,v1):
    pi = np.pi
    E=np.arange(Emin,2*max(V),GSize)
    K=[0 for x in range(len(E))]
    EK=[0 for x in range(len(E))]
    EKa=[0 for x in range(len(E))]
    for i in range(len(E)):
        C=(h**2)/(8*m*(L**2))     # calculation of C factor
        a=0.5*np.sqrt(E[i]/C)
        b=0.5*np.sqrt((E[i]-A)/C)
        d=0.5*np.sqrt((B-C)/C)
        K[i]=1-((np.cosh(2*pi*(a-b))+np.cosh(2*pi*d))/(np.cosh(2*pi*(a+b))+np.cosh(2*pi*d)))
        EK[i]=E[i]
        EKa[i]=E[i]*627.509
    
    G=[0 for x in range(len(T))]
    for q in range(len(T)):                                # Temperature dependent operations begin
        GK=[0 for x in range(len(K))]                                              # Clean variable
        for j in range(len(K)):                            
            GK[j]=K[j]*np.exp(-EK[j]/(kB*T[q]))           # Calculate integrand
        GI=[0 for x in range(len(K))]
        for l in range(len(EK)-1):
            GI[l]=(0.5*(GK[l]+GK[l+1])*abs(EK[l]-EK[l+1])) # Numerical integration by using the area of squares
        
        GI=np.sum(GI)
        GI=GI*(np.exp(v1/(kB*T[q]))/(kB*T[q]))                     # multiplying integral with prefactor
        
        # For energies above the interval where transmission is less than 1, we use
        # analytical integration to obtain the final result
        
        G[q]=GI+np.exp(v1/(kB*T[q]))*np.exp(-EK[len(EK)-1]/(kB*T[q]))
        
    return G, EKa, K, GK


def eckart(SP_TS, SP_reactant, SP_product, imag, T=[298.15]):
    c=2.99792458e+8          # Speed of light (m s-1)
    pi=np.pi               # π
    kB=3.1668152e-6
    h=2*pi
    Na=6.0221409e+23         # Avogadro's number (mol-1)

    E1 = SP_TS - SP_reactant
    E2 = SP_TS - SP_product
    mu = 1
    v1=((E1*4184)/Na)/4.3597447222071e-18
    v2=((E2*4184)/Na)/4.3597447222071e-18
    wau=(imag*100)*c*2.418884326509e-17
    m=mu*1822.888479

    # Calculate force constant, A, B and L
    F=-4*(pi**2)*(wau**2)*m;
    F2=-4*(pi**2)*(wau**2)*1;
    A=v1-v2;
    B=(np.sqrt(v2)+np.sqrt(v1))**2;
    L=-pi*(A-B)*(B+A)/(np.sqrt(-2*F*B)*B);

    # Defining reaction coordinate
    x = np.arange(-3, 3, 0.01)
    x = x/(np.sqrt(mu))      # Removing reduced mass from reaction coordinate in order to get a well defined potential

    # Calculating potential
    y=[0 for i in range(len(x))]
    V=[0 for i in range(len(x))]
    xa=[0 for i in range(len(x))]
    Va=[0 for i in range(len(x))]

    for i in range(len(x)):
        y[i]=-np.exp( (2*pi*x[i])/L )
        V[i]=( (-(y[i]*A)/(1-y[i]) ) - ( (y[i]*B)/((1-y[i])**2)) )
        xa[i]=0.529177*x[i]*np.sqrt(mu)         # reduced mass re-inserted for plotting
        Va[i]=V[i]*627.509                        # potential converted to kcal/mol for plotting

    # Calculating the correction factors for all T's
    VB=[0,0]
    VB[0]=V[0]                                                # value of potential at reactants
    VB[1]=V[len(x)-1]                                           # value of potential at products
    Emin=np.max(VB)                                           # minimum energy at which tunnelling can occur
    Gdiff=1                                                   # initial convergence control set to 1
    GSize=np.max(V)/50                                        # initial integration stepsize 
    [Gold,EKa,K,GK]=Gcalc(Emin,GSize,V,A,B,L,h,kB,m,T,v1)                      # calculate G
    GSize=GSize/10                                            # reduce integration stepsize
    runs=0                                                    # initial number of runs
    while Gdiff >= 0.001:                                        # convergence criteria
        [Gnew,EKa,K,GK]=Gcalc(Emin,GSize,V,A,B,L,h,kB,m,T,v1)  # new G
    #    print("Tunneling Factor", Gnew)                                         # display new correction factor
        GSize=GSize/10                                        # reduce integration stepsize
        Gdiffcalc=[0 for x in range(len(T))]
        for j in range(len(T)):
            Gdiffcalc[j]=abs(Gnew[j]-Gold[j])/Gold[j]         # calculate convergence
        Gdiff=max(Gdiffcalc)                                  # max convergence control value
    #    print("convergence control value", Gdiff)                                        # display convergence control value
        Gold=Gnew                                             # replace old correction factor with new
        runs=runs+1                                           # a run completed
    #    print("runs done", runs)                                         # display run number

    [G,EKa,K,GK]=Gcalc(Emin,GSize,V,A,B,L,h,kB,m,T,v1)        #final G

    kappa = G[0]
    return kappa


def rate_constant(TS_conformers, reactant_conformers, product_conformers, T=298.15):
    k_b = 1.380649e-23  # Boltzmann constant in J/K
    h = 6.62607015e-34  # Planck constant in J*s
    HtoJ = 43.597447222e-19  # Conversion factor from Hartree to Joules
    Htokcalmol = 627.509
    Na = 6.022e23 # molecules/mol
    liters_to_cm3 = 1000 # 1 liter is 1000 cm^3
    mol_per_liter = 1/(0.0821*T)
    p_ref = (mol_per_liter*Na)/liters_to_cm3
    kappa = 1
    
    # Calculation of rate constant
    if reactant_conformers and TS_conformers:
        # Seperate organic molecule and OH
        reactant_molecules = [mol for mol in reactant_conformers if 'OH' not in mol.name]
        OH = next((mol for mol in reactant_conformers if 'OH' in mol.name))

        # Find the lowest single point energies for reactant (excluding OH) and TS
        lowest_reactant = min(reactant_molecules, key=lambda molecule: molecule.zero_point_corrected)
        lowest_TS = min(TS_conformers, key=lambda molecule: molecule.zero_point_corrected)

        # Lowest single point reactant and TS and convert from Hartree to joules and kcal/mol
        lowest_ZP_TS_J = np.float64(lowest_TS.zero_point_corrected * HtoJ)
        lowest_ZP_reactant_J = np.float64(lowest_reactant.zero_point_corrected * HtoJ)
        lowest_ZP_TS_kcalmol = np.float64(lowest_TS.zero_point_corrected * Htokcalmol)
        lowest_ZP_reactant_kcalmol = np.float64((lowest_reactant.zero_point_corrected + OH.zero_point_corrected) * Htokcalmol)
        print(f"Ea: {lowest_ZP_TS_kcalmol - lowest_ZP_reactant_kcalmol} kcal/mol")

        if OH:
            sum_reactant_ZP_J = lowest_ZP_reactant_J + (np.float64(OH.zero_point_corrected * HtoJ))
            Q_reactant = np.sum([np.exp(-(lowest_ZP_reactant_J - np.float64(mol.zero_point_corrected * HtoJ)) / (k_b * T)) * np.float64(mol.Q) for mol in reactant_molecules]) * np.float64(OH.Q)
        else:
            sum_reactant_ZP_J = lowest_ZP_reactant_J
            Q_reactant = np.sum([np.exp(-(lowest_ZP_reactant_J - np.float64(mol.zero_point_corrected * HtoJ)) / (k_b * T)) * np.float64(mol.Q) for mol in reactant_molecules])        

        Q_TS = np.sum([np.exp(-(lowest_ZP_TS_J - np.float64(mol.zero_point_corrected * HtoJ)) / (k_b * T)) * np.float64(mol.Q) for mol in TS_conformers])

        if product_conformers:
            # Seperate organic molecule and H2O
            product_molecules = [mol for mol in product_conformers if 'H2O' not in mol.name]
            H2O = next((mol for mol in product_conformers if 'H2O' in mol.name), None)

            if product_molecules and H2O:
                lowest_product = min(product_molecules, key=lambda molecule: molecule.zero_point_corrected)
                lowest_ZP_product_kcalmol = np.float64((lowest_product.single_point + H2O.single_point) * Htokcalmol)

                imag = abs(lowest_TS.vibrational_frequencies[0])
                kappa  = eckart(lowest_ZP_TS_kcalmol, lowest_ZP_reactant_kcalmol, lowest_ZP_product_kcalmol, imag)
                k = kappa * (k_b*T)/(h*p_ref) * (Q_TS/Q_reactant) * np.exp(-(lowest_ZP_TS_J - sum_reactant_ZP_J) / (k_b * T))
            else:
                print("Error in product molecules")
                return None
        else: # If products are not calculated assume tunneling coefficient is 1
            k = kappa * (k_b*T)/(h*p_ref) * (Q_TS/Q_reactant) * np.exp(-(lowest_ZP_TS_J - sum_reactant_ZP_J) / (k_b * T))

        return k, kappa # cm^3 molecules^-1 s^-1

    return None


def main():
    parser = argparse.ArgumentParser(description='''   -Dynamic Approach for Transition State-
    Automated tool for generating input files, primarily 
    for transition state geometry optimization. 
    Calculation of tunneling corrected multi-configurational 
    rate constants can also be calculated from log files.''',
                                     prog="JKTS",
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog='''
    Examples of use:
                JKTS pinonaldehyde.xyz -OH -auto false
                JKTS CH4.xyz -OH -auto --low_method "am1 3-21g" --high_method "B3LYP 6-31+g(d,p)"
                JKTS CH4_H1_opt_constrain.pkl -info
                JKTS benzene.xyz -OH -ORCA -par qtest -auto false
                JKTS *TS.log -time 5:00:00
                                     ''')

    parser.add_argument('input_files', metavar='reactant.xyz', nargs='*', help='Input XYZ files (e.g., pinonaldehyde.xyz)')
    parser.add_argument('-G16', action='store_true', help='Use Gaussian16 for QC calculations (default)')
    parser.add_argument('-ORCA', action='store_true', help='Use ORCA for QC calculations')
    parser.add_argument('-constrain', type=str2bool, metavar='<boolean>', default=True, help='Integrate constraints into relevant input files [def: True]')
    parser.add_argument('-reactants', type=str2bool, default=True, metavar='<boolean>', help='Prepare folder for reactants [def: True]')
    parser.add_argument('-products', type=str2bool, default=True, metavar='<boolean>', help='Prepare folder for products [def: True]')
    parser.add_argument('-transition_states', type=str2bool, default=True, metavar='<boolean>', help=argparse.SUPPRESS)
    parser.add_argument('-NEB', type=str2bool, default=False, metavar='<boolean>', help='Prepare input file for Nudged Elastic Band')
    parser.add_argument('-auto', type=str2bool, default=True, metavar='<boolean>', help='Automated process with the following workflow:\n- CREST conformer sampling of xyz input file (GFN2-xTB -ewin=8 kcal/mol)\n- Preoptimization of geometry (Low Level of Theory)\n- Optimization towards transition state (High Level of Theory)\n- DLPNO-CCSD(T) SP energy calculations on top of TS conformers\n- Calculate rate constants and branching ratios for reaction type\n* Automatically resubmit failed calculations until convergence or wall-time limit is reached')

    # Argparse reactions
    reaction_options = parser.add_argument_group("Types of reactions")
    reaction_options.add_argument('-OH', action='store_true', help='Perform H abstraction with OH radical')
    reaction_options.add_argument('-CC (TBA)', action='store_true', help='Perform addition to C=C bonds')
    reaction_options.add_argument('-OH_CC (TBA)', action='store_true', help='Perform OH addition to C=C bonds')

    # Argparse additional
    additional_options = parser.add_argument_group("Additional arguments")
    # additional_options.add_argument('-restart', action='store_true', default=False, help='Restart and resume log files')
    additional_options.add_argument('-k', type=str2bool, metavar='<boolean>', default=True, help='Calculate Multiconformer Transition State rate constant [def: True]')
    additional_options.add_argument('-info', action='store_true', default=False, help='Print information of molecules in log files or .pkl file')
    parser.add_argument('-CHO', dest='CHO', action=ParseCHO, nargs='*', help="Set indexes of atoms for active site. Indexing starting from 1")
    additional_options.add_argument('-collect', action='store_true', default=False, help='Collect thermochemical data from TS structures and single point correction from DLPNO')
    additional_options.add_argument('--high_level', nargs='+', metavar='', help='Specify high-level theory for QC method TS optimization [def: uwB97X-D aug-cc-pVTZ]')
    additional_options.add_argument('--low_level', nargs='+', metavar='', help='Specify low-level theory for preoptimization [def: B3LYP 6-31+G(d,p)]')
    additional_options.add_argument('--gfn', default='2', choices=['1','2'], help='Specify the GFN version (1 or 2, default: 2)')
    additional_options.add_argument('-skip_low', action='store_true', default=False, help='Skip the preoptimization of the structures at the low level of theory')
    additional_options.add_argument('-filter_step', type=str, default='DLPNO', help='Perform filtering using ArbAlign before [step] in the workflow [def: DLPNO]')
    additional_options.add_argument('-cpu', metavar="int", nargs='?', const=1, type=int, default=4, help='Number of CPUs [def: 4]')
    additional_options.add_argument('-mem', metavar="int", nargs='?', const=1, type=int, default=8000, help='Amount of memory allocated for the job [def: 8000MB]')
    additional_options.add_argument('-par', metavar="partition", type=str, default="q24,q28,q36,q40,q48,q64", help='Partition to use [def: qany]')
    additional_options.add_argument('-time', metavar="hh:mm:ss", type=str, default=None, help='Monitoring duration [def: 144 hours]')
    additional_options.add_argument('-interval', metavar="int", nargs='?', const=1, type=int, help='Time interval between log file checks [def: based on molecule size]')
    additional_options.add_argument('-initial_delay', metavar="int", nargs='?', const=1, type=int, help='Initial delay before checking log files [def: based on molecule size]')
    additional_options.add_argument('-attempts', metavar="int", nargs='?', const=1, type=int, default=100, help='Number of log file check attempts [def: 100]')
    additional_options.add_argument('-max_conformers', metavar="int", nargs='?', const=1, type=int, default=50, help='Maximum number of conformers from CREST [def: 50]')
    additional_options.add_argument('-freq_cutoff', metavar="int", nargs='?', const=1, type=int, default=-120, help='TS imaginary frequency cutoff [def: -120 cm^-1]')
    additional_options.add_argument('-ewin', metavar="int", nargs='?', const=1, default=8, type=int, help='Energy threshold for CREST conformer sampling [def: 8 kcal/mol]')
    additional_options.add_argument('-energy_cutoff', metavar="int", nargs='?', const=1, default=5, type=int, help='After preoptimization, remove conformers which are [int] kcal/mol higher in energy than the lowest conformer [def: 5 kcal/mol]')
    additional_options.add_argument('-filter', type=str2bool, metavar='<boolean>', default=True, help='Filter identical conformers after transition state optimization [def: True]')

    additional_options.add_argument('-test', action='store_true', default=False, help=argparse.SUPPRESS) # Run TEST section in main() and exit
    additional_options.add_argument('-num_molecules', type=int, default=None, help=argparse.SUPPRESS) # Set the number of directories created to [int]. Used when doing limited testing
    additional_options.add_argument('-XQC', '-YQC', '-QC', action=SCFAction, nargs='*', const=[], default='SCF=(XQC)', dest='SCF', help='Use G16 SCF=(X,Y)QC algorithm for SCF procedure - https://gaussian.com/scf/')
    parser.set_defaults(SCF="")


    hidden_options = parser.add_argument_group()
    hidden_options.add_argument('-init', action='store_true', help=argparse.SUPPRESS)
    hidden_options.add_argument('-loc', action='store_true', help=argparse.SUPPRESS)
    hidden_options.add_argument('-reaction_angle', metavar="float", nargs='?', default=169.5, const=1, type=float, help=argparse.SUPPRESS) 
    additional_options.add_argument('-skip', type=str2bool, default=False, help=argparse.SUPPRESS)

    global args, start_dir
    args = parser.parse_args()
    start_dir = os.getcwd()

    ################################ARGUMENT SPECIFICATIONS############################

    # Program and method 
    global low_method, low_basis, high_method, high_basis

    methods_no_basis = {"b97-3c", "r2scan-3c", "pm3", "am1", "pm6", "pm7"}

    def extract_method_basis(input_args, default):
        if not input_args:
            return default

        if len(input_args) == 1:
            if input_args[0].lower() in methods_no_basis:
                return input_args[0], ""
            else:
                raise ValueError(f"Basis set required for method {input_args[0]}")
        
        return input_args[0], input_args[1]

    standard_high_level = ["uwb97xd", "6-31++g(d,p)"]
    standard_low_level = ["uwb97xd", "6-31+g(d,p)"]

    high_method, high_basis = extract_method_basis(args.high_level, standard_high_level)
    low_method, low_basis = extract_method_basis(args.low_level, standard_low_level)

    global global_program
    if args.ORCA:
        global_program = "ORCA"
        if low_method in methods_no_basis:
            low_basis = ""
        if high_method in methods_no_basis:
            high_basis = ""
        if high_method.lower() == "uwb97xd":
            high_method = "WB97X-D3"
        if low_method.lower() == "uwb97xd":
            low_method = "WB97X-D3" 
    elif args.G16:
        global_program = "G16"
    else: 
        global_program = "G16" # Default case

    global termination_strings, error_strings
    termination_strings = {
        "g16": "Normal termination",
        "orca": "****ORCA TERMINATED NORMALLY****",
        "crest": "" # " CREST terminated normally."
    }
    error_strings = {
        "g16": "Error termination",
        "orca": "ORCA finished by error termination",
        "crest": "Find my error message"
    }

    #####################################################################################################
    if args.test:
        molecules = []
        threads = []
        logger = Logger(os.path.join(start_dir, "log_test"))
        for n, input_file in enumerate(args.input_files, start=1):
            molecule = Molecule(input_file, indexes=args.CHO)
            molecules.append(molecule)
            # molecule.constrained_indexes = {'C': 6, 'H': 21, 'O': 28, 'OH': 29}
            print(molecule.name, check_transition_state(molecule, logger))



        exit()
    ####################################################################################################

    threads = []
    input_molecules = []
    final_TS = []
    final_reactants = []
    final_products = []
    molecules = []

    if args.init: # only executes if input file is an xyz file
        input_file = args.input_files[0]
        file_name, file_type = os.path.splitext(input_file)
        input_file_path = os.path.join(start_dir, input_file)
        input_molecule = Molecule(input_file_path, reactant=True) # Reuse input molecule as template for reactant

        if args.OH:
            reacted_molecules, product_molecules = input_molecule.H_abstraction(NEB=args.NEB, products=args.products, num_molecules=args.num_molecules)
        elif args.CC:
            other_molecule = args.input_files[1]
            reacted_molecules = input_molecule.addition(other_molecule)
        else:
            parser.error("Need to specify reaction type")

        if args.transition_states:
            for count, molecule in enumerate(reacted_molecules, start=1):
                molecule.name = f"{file_name}_H{count}" 
                molecule.directory = os.path.join(start_dir, molecule.name)
                mkdir(molecule)
                logger = Logger(os.path.join(molecule.directory, "log"))
                molecule.write_xyz_file(os.path.join(molecule.directory, f"{molecule.name}.xyz")) 
                molecule.save_to_pickle(os.path.join(molecule.directory, f"{molecule.name}.pkl"))

        if args.reactants: 
            reactant_dir = os.path.join(start_dir, 'reactants')
            input_molecule.name = f"{file_name}_reactant"
            input_molecule.directory = reactant_dir
            input_molecule.set_current_step('crest_sampling')
            mkdir(input_molecule, crest_constrain=False)
            input_molecule.save_to_pickle(os.path.join(input_molecule.directory, f"{input_molecule.name}.pkl"))

        if args.products and product_molecules:
            product_dir = os.path.join(start_dir, 'products')
            for count, molecule in enumerate(product_molecules, start=1):
                molecule.name = f"{file_name}_product_H{count}"
                molecule.directory = product_dir
            mkdir(product_molecules[0], crest_constrain=False)
            pickle_path = os.path.join(product_dir, "product_molecules.pkl")
            Molecule.molecules_to_pickle(product_molecules, pickle_path)

    elif args.info:
        for input_file in args.input_files:
            file_name, file_type = os.path.splitext(input_file)
            if file_type == '.pkl':
                molecules = Molecule.load_molecules_from_pickle(input_file)
                if isinstance(molecules, pd.DataFrame):
                    conformers = pkl_to_xyz(input_file)
                    conformers = initiate_conformers(conformers, input_file) 
                    for conf in conformers:
                        conf.print_items()
                else:
                    if isinstance(molecules, list):
                        for molecule in molecules:
                            molecule.print_items()
                    else: molecules.print_items()
            elif file_type in ['.log', '.out', '.com', '.inp']:
                if file_type in ['.out', '.inp']: global_program = 'ORCA'
                input_file_path = os.path.join(start_dir, input_file)
                molecule = Molecule(input_file_path, indexes=args.CHO)
                molecule.print_items()
            else:
                parser.error("Invalid input file format. The program expects input files with extensions '.pkl', '.log', or '.out'. Please ensure that your input file is in one of these formats and try again. If you provided a different type of file, convert it to a supported format or select an appropriate file for processing.")

    else: # We loop over all molecules given in the argument input and process them according to file type
        for n, input_file in enumerate(args.input_files, start=1):
            file_name, file_type = os.path.splitext(input_file)
            if file_type in ['.out', 'inp']: global_program = 'ORCA'

            if file_type == '.xyz':
                if os.path.basename(start_dir) == 'reactants':
                    input_file_path = os.path.join(start_dir, f"{file_name}_reactant.pkl")
                    logger = Logger(os.path.join(start_dir, "log"))
                    reactant = Molecule.load_from_pickle(input_file_path)
                    molecules.append(reactant)

                elif os.path.basename(start_dir) == 'products':
                    logger = Logger(os.path.join(start_dir, "log"))
                    pickle_path = os.path.join(start_dir, "product_molecules.pkl")
                    product_molecules = Molecule.load_molecules_from_pickle(pickle_path)
                    for product in product_molecules:
                        product.product = True
                        product.current_step = 'crest_sampling'
                        molecules.append(product)
                else:
                    input_file_path = os.path.join(start_dir, os.path.basename(start_dir)+'.pkl')
                    molecule = Molecule.load_from_pickle(input_file_path)
                    logger = Logger(os.path.join(start_dir, "log"))
                    molecules.append(molecule)

                handle_termination(molecules, logger, threads, converged=False)

    ### If input files are pickle or log files we collect them into input_molecules and process the list, by checking each input convergence status
            elif file_type == '.pkl':
                df = pd.read_pickle(input_file)
                if isinstance(df, pd.DataFrame): # If pkl file is from the CREST job it will come as a pandas dataframe, instead of a list
                    conformer_molecules = pkl_to_xyz(input_file)
                    conformer_molecules = initiate_conformers(conformer_molecules, input_file)
                    for m in conformer_molecules:
                        input_molecules.append(m)
                else:
                    molecules = Molecule.load_molecules_from_pickle(input_file)
                    if not isinstance(molecules, list):
                        molecules = [molecules]
                    for m in molecules: 
                        if m.current_step == 'Done' or m.current_step == 'DLPNO':
                            if m.reactant:
                                final_reactants.append(m)
                            elif m.product:
                                final_products.append(m)
                            else:
                                final_TS.append(m)
                        else:
                            input_molecules.append(m)

            elif file_type in [".log", ".out", ".com", ".inp"]: 
                # Initialize molecule from log file
                input_file_path = os.path.join(start_dir, input_file)
                molecule = Molecule(file_path=input_file_path, indexes=args.CHO)
                input_molecules.append(molecule)

            else:
                print(f"File type {file_type} is not supported")
                exit()

        if final_TS and final_reactants: # In the case final_products is empty symmetrical eckart tunneling used
            k, kappa = rate_constant(final_TS, final_reactants, final_products)
            print(f"{k} cm^3 molecules^-1 s^-1")
            print(f"Tunneling coefficient: {kappa}")
            exit()


        logger = Logger(os.path.join(start_dir, "log"))
        if input_molecules and file_type != '.xyz':
            if args.collect:
                collected_molecules = collect_DFT_and_DLPNO(input_molecules)
                Molecule.molecules_to_pickle(collected_molecules, os.path.join(start_dir, "collected_molecules.pkl"))
            else:
                handle_input_molecules(input_molecules, logger, threads)
                    
            # else:
            #     logger.log("Error when generating input molecules. Could not create list from given input")
            #     print("Error when generating input molecules. Could not create list from given input")
            #     if file_type in [".log", ".out"]:
            #         logger.log(".log or .out extension detected. Make sure input files are from ORCA or G16")
            #     elif file_type == '.pkl':
            #         logger.log("Detected .pkl file. Make sure the structure of the pickle file is either a python list, set, tuple or pandas.DataFrame")

    # Monitor and handle convergence of submitted jobs
    while threads:
        for thread in list(threads):
            thread.join(timeout=0.1)
            if not thread.is_alive():
                threads.remove(thread)

    if global_molecules:
        logger = Logger(os.path.join(start_dir, "log"))
        molecules_logger = Logger(os.path.join(start_dir, "molecules.txt"))
        for molecule in global_molecules: 
            molecule.log_items(molecules_logger)

        if all(m.reactant for m in global_molecules):
            molecule_name = global_molecules[0].name.split("_")[0]
            logger.log(f"Final DLPNO calculations for reactants is done. Logging molecules to Final_reactants_{molecule_name}.pkl")
            Molecule.molecules_to_pickle(global_molecules, os.path.join(start_dir, f"Final_reactants_{molecule_name}.pkl"))
        elif all(m.product for m in global_molecules):
            # Identify unique H numbers
            h_numbers = sorted(set(m.name.split('_H')[1][0] for m in global_molecules if "_H" in m.name))
            # Group molecules by H numbers and include H2O in each group
            grouped_lists = [[m for m in global_molecules if f"_H{h_num}_" in m.name] + 
                             [m for m in global_molecules if "H2O" in m.name] 
                             for h_num in h_numbers]

            # Create a pickle file for each group
            for h, molecules_group in zip(h_numbers, grouped_lists):
                molecule_name = f"{molecules_group[0].name.split('_')[0]}_H{h}"
                pickle_path = os.path.join(start_dir, f"Final_products_{molecule_name}.pkl")
                logger.log(f"Final DLPNO calculations for products are done. Logging properties to Final_products_{molecule_name}.pkl")
                Molecule.molecules_to_pickle(molecules_group, pickle_path)
        else:
            molecule_name = os.path.basename(start_dir)
            logger.log(f"Final DLPNO calculations for transition state molecules is done. Logging properties to Final_TS_{molecule_name}.pkl")
            TS_pkl_path = os.path.join(start_dir, f"Final_TS_{molecule_name}.pkl")
            Molecule.molecules_to_pickle(global_molecules, TS_pkl_path)

            if args.k:
                reactant_pkl_name = os.path.basename(start_dir).split("_")[0]
                product_pkl_name = os.path.basename(start_dir)
                reactant_pkl_path = os.path.join(os.path.dirname(start_dir), f'reactants/Final_reactants_{reactant_pkl_name}.pkl')
                product_pkl_path = os.path.join(os.path.dirname(start_dir), f'products/Final_products_{product_pkl_name}.pkl')
                if os.path.exists(reactant_pkl_path) and os.path.exists(product_pkl_path):
                    final_reactants = Molecule.load_molecules_from_pickle(reactant_pkl_path)
                    final_products = Molecule.load_molecules_from_pickle(product_pkl_path)
                    k, kappa = rate_constant(global_molecules, final_reactants, final_products)
                    results_logger = Logger(os.path.join(os.path.dirname(start_dir), "Rate_constants.txt"))
                    results_logger.log_with_stars(f"{k} molecules cm^-3 s^-1 with tunneling coefficient: {kappa}")
                else:
                    reactant_pkl_path = os.path.join(start_dir, f'reactants/Final_reactants_{reactant_pkl_name}.pkl')
                    product_pkl_path = os.path.join(start_dir, f'products/Final_products_{product_pkl_name}.pkl')
                    if os.path.exists(reactant_pkl_path) and os.path.exists(product_pkl_path):
                        final_reactants = Molecule.load_molecules_from_pickle(reactant_pkl_path)
                        final_products = Molecule.load_molecules_from_pickle(product_pkl_path)
                        k, kappa = rate_constant(global_molecules, final_reactants, final_products)
                        results_logger = Logger(os.path.join(start_dir, "results_log"))
                        results_logger.log_with_stars(f"{k} molecules cm^-3 s^-1 with tunneling coefficient: {kappa}")
                    else:
                        logger.log("Done")


if __name__ == "__main__":
    main()