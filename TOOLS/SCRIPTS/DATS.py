#!/usr/bin/env python3
'''Dynamic Approach for Transition State'''
###############################LIBRARIES#####################################
import numpy as np
import pandas as pd
import argparse
import os
import shutil
import re
# import pickle
# from ase.io import read

###############################CONSTANS#####################################
T = 298.15
h = 6.62607015e-34
k_b = 1.380649e-23
c = 29979245800
R = 8.314 # J/mol*K 

maxtasks = 100

###########################VECTOR MANIPULATION################################
def calculate_vector(coord1, coord2):
    return np.array(coord2) - np.array(coord1)

def vector_length(vector):
    return np.linalg.norm(vector)

def atom_distance(atom1, atom2):
    return np.linalg.norm(np.array(atom2) - np.array(atom1))

def normalize_vector(vector):
    norm = np.linalg.norm(vector)
    if norm < 1e-8: 
        return np.zeros_like(vector)
    return vector / norm

def rotate_vector(vector, axis, angle):
    cos_theta = np.cos(angle)
    sin_theta = np.sin(angle)
    cross_product = np.cross(axis, vector)
    rotated_vector = (vector * cos_theta +
                      cross_product * sin_theta +
                      axis * np.dot(axis, vector) * (1 - cos_theta))
    return rotated_vector

#########################################FILES MANIPULATION############################
def read_xyz_file(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()[2:]
        coords = [[line.split()[0], float(line.split()[1]), float(line.split()[2]), float(line.split()[3])] for line in lines]
    return coords

def write_xyz_file(file_path, updated_coords):
    with open(file_path, 'w') as f:
        f.write(str(len(updated_coords)) + '\n' +'\n')
        for atom in updated_coords:
            f.write(f'{atom[0]} {atom[1]:.6f} {atom[2]:.6f} {atom[3]:.6f}\n')


def mkdir(file, index: list, crest):
    cwd = os.getcwd() # Current working directory
    dir_name = os.path.splitext(file)[0]
    new_dir = os.path.join(cwd, dir_name)
    if os.path.exists(new_dir):
        shutil.move(cwd + "/" + file, new_dir + "/" + file) # NOTE: overwrites already existing files with same name in new_dir. As the full path is given
        if crest:
            crest_constrain(new_dir, *index)
    else:
        os.mkdir(new_dir)
        shutil.move(file, new_dir)
        if crest:
            crest_constrain(new_dir, *index)

    TS_commands(file, new_dir)
    with open(new_dir + "/.constrain", "w") as c:
        c.write(f"{index[0]}, {index[1]}, {index[2]}") # C, H, O
        


def TS_commands(file, dir):
    with open(dir + "/commands.txt", "w") as f:
        file_name = file.rsplit('.', 1)[0]
        f.write(f'sbatch -J {file_name} -p {args.par} --mem={args.mem} -c {args.cpu} JKsend "source ~/.JKCSusersetup.txt; program_CREST {dir}/{file_name}.xyz -gfn2 -ewin 2 -noreftopo -cinp {dir}/constrain.inp -uhf 1"\n')
        f.write(f'rm {file}\n')
        f.write(f'rm {file_name}.xyz\n')
        f.write(f'JKTS collection{file_name}.pkl -{program} -method B3LYP -basis "6-31+g(d,p)" --no-TS -constrain --no-xyz\n')
        f.write(f'ls "$(pwd)"/*.com > array.txt\n')
        f.write(f'JKCS3_run -p {program} -cpu {args.cpu} -mem {args.mem} -par {args.par} -maxtasks {maxtasks} -mult 2 -rf array.txt -nf preopt_DFT\n')
        f.write(f'cp preopt_DFT/calc-LM/*.log .\n')
        f.write(f'rm *.com\n')
        f.write(f'rm *.xyz\n')
        f.write(f'JKlog2xyz\n')
        f.write(f'JKTS *.xyz -{program} -method {args.method} -basis "{args.basis}" -par {args.par}\n')
        f.write(f'ls "$(pwd)"/*.com > array.txt\n')
        f.write(f'rm *.xyz\n')
        f.write(f'JKCS3_run -p {program} -cpu {args.cpu} -mem {args.mem} -par {args.par} -maxtasks {maxtasks} -mult 2 -rf array.txt -nf DFT_TS\n')
        f.write(f'cp DFT_TS/calc-LM/*.log .\n')
        f.write(f'rm *.com\n')
        f.write(f'JKlog2xyz\n')
        f.write(f'ls "$(pwd)"/*.xyz > array.txt\n')
        f.write(f'JKCS3_run -p {program} -cpu {args.cpu} -mem {args.mem} -par {args.par} -maxtasks {maxtasks} -mult 2 -rf array.txt -nf DLPNO_SP -m "aug-cc-pVTZ aug-cc-pVTZ/C DLPNO-CCSD(T) TightSCF RI-JK aug-cc-pVTZ/JK"\n')


def reactant_folder(file):
    cwd = os.getcwd()
    reactant_dir = os.path.join(cwd, "reactants")
    if os.path.exists(reactant_dir):
        shutil.copy2(cwd + "/" + file, reactant_dir + "/" + file)
    else:
        os.mkdir(reactant_dir)
        shutil.copy2(file, reactant_dir)
    with open(reactant_dir + "/commands.txt", "w") as f:
        f.write(f'sbatch -J {file} -p {args.par} --mem={args.mem} -c {args.cpu} JKsend "source ~/.JKCSusersetup.txt; program_CREST {reactant_dir}/{file} -gfn2 -ewin 2 -noreftopo"\n')
        f.write(f'rm {file}\n')
        f.write(f'JKTS collection{file.split(".")[0]}.pkl -{program} -method {args.method} -basis "{args.basis}" --no-xyz\n')
        f.write(f'ls "$(pwd)"/*.com > array.txt\n')
        f.write(f'JKCS3_run -p {program} -cpu {args.cpu} -mem {args.mem} -par {args.par} -maxtasks {maxtasks}  -rf array.txt -nf DFT_TS\n')

def pkl_to_xyz(file):
    all_conformers = []
    with open(file, 'rb') as f:
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
    return all_conformers

#########################################GENERATE INPUT FILES#############################
def crest_constrain(file_path, C_index, H_index, O_index, force_constant=0.95):
    '''Force constant tells how tight to constrain the atoms i.e. the magnitude of the oscillation between the constrained atoms'''
    with open (file_path + "/constrain.inp","w") as f:
        f.write("$constrain\n")
        f.write(f"  force constant={force_constant}\n") 
        f.write(f"  distance: {C_index}, {H_index}, auto\n")
        f.write(f"  distance: {H_index}, {O_index}, auto\n")
        f.write(f"  angle: {C_index}, {H_index}, {O_index}, auto\n")
        f.write("$end\n")


def QC_input(file_path, coords, TS, constrain, program, method, basis_set, C_index=None, H_index=None, O_index=None):
    if constrain and C_index == None and H_index == None and O_index == None:
        with open(os.getcwd() + "/.constrain", "r") as f:
            content = f.read()
            C_index, H_index, O_index = [int(num) for num in content.split(",")]
            
    if program == "ORCA":
        file_path = file_path + ".inp"
        with open(file_path, "w") as f:
            if TS:
                f.write(f"! {method} {basis_set} OptTS freq\n")
            else:
                f.write(f"! {method} {basis_set} Opt\n")
            f.write("%pal nprocs 8 end\n")
            f.write("%maxcore 8000\n")
            if constrain:
                f.write("%geom\n")
                f.write("Constraints\n")
                # ORCA start atom indexing from 0
                f.write(f"{{B {C_index-1} {H_index-1} C}}\n") # Bond being broken
                f.write(f"{{B {H_index-1} {O_index-1} C}}\n") # Bond being formed
                f.write("end\nend\n")
            f.write("\n")
            f.write("* xyz 0 2\n") # charge and spin multiplicity usually 0 and 2 for atmospheric radical reaction
            for atom in coords:
                f.write(f'{atom[0]} {atom[1]:.6f} {atom[2]:.6f} {atom[3]:.6f}\n')
            f.write("*")
    elif program == "G16":
        file_path = file_path + ".com"
        with open(file_path, "w") as f:
            f.write("%nprocshared=4\n")
            f.write("%mem=4GB\n")
            if TS and constrain:
                f.write(f"# {method} {basis_set} opt=(calcfc,ts,noeigen,modredundant) freq\n\n") # freq may be redundant
            elif TS and constrain is False:
                f.write(f"# {method} {basis_set} opt=(calcfc,ts,noeigen) freq\n\n")
            elif TS is False and constrain:
                f.write(f"# {method} {basis_set} opt=modredundant\n\n")
            else:
                f.write(f"# {method} {basis_set} opt\n\n")
            f.write("Title\n\n")
            f.write("0 2\n")
            for atom in coords:
                f.write(f'{atom[0]} {atom[1]:.6f} {atom[2]:.6f} {atom[3]:.6f}\n')
            f.write("\n")
            if constrain:
                f.write(f"B {C_index} {H_index} F\n")
                f.write(f"B {H_index} {O_index} F\n")
                f.write("\n")
    else:
        print("QC_input was called but no program was specified")

##############################ADDITIONAL FUNCTIONS##################################
def get_terminal_O(coords, distance=1.5):
    O_index = []
    for i,atom in enumerate(coords):
        if atom[0] == "O":
            neighbors = sum(1 for j, other_atom in enumerate(coords) if i != j and atom_distance(atom[1:], other_atom[1:]) < distance)
            if neighbors == 1:
                O_index.append(i)
    return O_index[0]

def number_lines(file):
    line_count = 0
    with open(file, "r") as file:
        for line in file:
            line_count += 1
    return line_count

#####################################MAIN FUNCTIONS####################################
def H_abstraction(file, method, basis_set, TS, no_xyz, crest, program=None, distance=1.35, dist_OH=0.97, constrain=False):
    coords = read_xyz_file(file)
    num_atoms = len(coords)
    count = 1

    for i in range(num_atoms):
        if coords[i][0] == "H":  # Atom label for hydrogen
            for j in range(num_atoms):
                if coords[j][0] == "C":  # Atom label for carbon
                    vector_CH = np.array(coords[i][1:]) - np.array(coords[j][1:])
                    dist_CH = vector_length(vector_CH)

                    if dist_CH < distance:
                        norm_vector_CH = normalize_vector(vector_CH)

                        H_perturb_axis = np.cross(norm_vector_CH, [0, 1, 0])
                        H_perturb_axis = normalize_vector(H_perturb_axis)
                        
                        # Update the initial hydrogen coordinates
                        new_coords = read_xyz_file(file)
                        new_coords[i][1:] = np.array(new_coords[i][1:]) + norm_vector_CH * (distance - dist_CH)

                        # Update oxygen in OH coordiantes
                        oxygen_coords = np.array(new_coords[i][1:]) + norm_vector_CH * distance

                        # Coordinates for hydrogen in OH
                        norm_vector_OH = normalize_vector(oxygen_coords - np.array(coords[i][1:]))
                        rotation_axis = np.cross(norm_vector_OH, H_perturb_axis)
                        rotation_axis = normalize_vector(rotation_axis)

                        rotation_angle_H = np.radians(75.5) # 180 - 75.5 = 104.5

                        rotated_vector_H = rotate_vector(norm_vector_CH, rotation_axis, rotation_angle_H)
                        hydrogen_coords = oxygen_coords + rotated_vector_H * dist_OH
                        
                        # Perturb inital hydrogen to avoid linear C--H--O
                        new_coords[i][1:] = np.array(new_coords[i][1:]) + H_perturb_axis * 0.3

                        # Update XYZ file
                        new_coords.append(['O', *oxygen_coords])
                        new_coords.append(['H', *hydrogen_coords])

                        C_index = j+1
                        H_index = i+1
                        O_index = len(new_coords)-1
                        # OH_index = len(new_coords)
                        list_index = [j+1, i+1, len(new_coords)-1] # ['C', 'H', 'O']

                        base_file_name = os.path.splitext(os.path.basename(file))[0]
                        if no_xyz is False:
                            write_xyz_file(f"{base_file_name}_H{count}.xyz", new_coords)
                            mkdir(f"{base_file_name}_H{count}.xyz", list_index, crest=crest)

                        if program != None:
                            QC_input(file_path=f"{base_file_name}_H{count}", coords=new_coords,C_index=C_index, H_index=H_index, O_index=O_index, constrain=constrain, TS=TS, method=method, basis_set=basis_set, program=program)
                            if program == "ORCA":
                                mkdir(f"{base_file_name}_H{count}.inp", list_index, crest=crest)
                            elif program == "G16": 
                                mkdir(f"{base_file_name}_H{count}.com", list_index, crest=crest)
                            else:
                                print("QC input files not generated since no program specified")

                        count += 1


def OH_addition(file, distance=1.45, double_bond_distance=1.36, dist_oh=0.97):
    coords = read_xyz_file(file)
    num_atoms = len(coords)
    count = 1

    for i in range(num_atoms):
        if coords[i][0] == "c":
            for j in range(num_atoms):
                if coords[j][0] == "c" and i != j:
                    vector_cc = np.array(coords[i][1:]) - np.array(coords[j][1:])
                    dist_cc = vector_length(vector_cc)
                    if dist_cc <= double_bond_distance:
                        norm_vector_cc = normalize_vector(vector_cc)
                        new_coords = read_xyz_file(file)
                        # note: update the way perpendicular axis is calculated. copy from addition()
                        perpendicular_axis = np.cross(norm_vector_cc, [0,1,0])
                        perpendicular_axis = normalize_vector(perpendicular_axis)
                        # shift carbon
                        new_coords[i][1:] = np.array(new_coords[i][1:]) + norm_vector_cc * 0.1
                        # update oxygen in oh coordiantes
                        oxygen_coords = np.array(new_coords[j][1:]) + perpendicular_axis * distance

                        rotation_axis = norm_vector_cc
                        rotation_angle_h = np.radians(45) 

                        rotated_vector_h = rotate_vector(perpendicular_axis, rotation_axis, rotation_angle_h)

                        hydrogen_coords = oxygen_coords + rotated_vector_h * dist_oh
                        
                        new_coords.append(['o', *oxygen_coords])
                        new_coords.append(['h', *hydrogen_coords])

                        base_file_name = os.path.splitext(os.path.basename(file))[0]
                        write_xyz_file(f"{base_file_name}_{count}.xyz", new_coords)

                        count += 1


def addition(file1, file2, method, basis_set, TS, no_xyz, program=None, constrain=False, distance = 1.55, double_bond_distance=1.36, elongation_factor=0.1):
    coords1 = read_xyz_file(file1)
    coords2 = read_xyz_file(file2)
    num_atoms1 = len(coords1)
    num_atoms2 = len(coords2)
    count = 1
    carbon_index = []
    vector_lst = []
    
    # gather carbon index
    for i in range(num_atoms1):
        if coords1[i][0] == "c":
            for j in range(num_atoms1):
                if atom_distance(coords1[i][1:], coords1[j][1:]) < 1.52:
                    perpendicular_vector = (coords1[i][1:], coords1[j][1:])
                    vector_lst.append(perpendicular_vector)
                if coords1[j][0] == "c" and i != j:
                    bond_cc = atom_distance(coords1[i][1:], coords1[j][1:])
                    if bond_cc <= double_bond_distance:
                        carbon_index.append(i)
                        carbon_index.append(j)

    for index in carbon_index[::2]:
        new_coords = read_xyz_file(file1)
        direction = calculate_vector(new_coords[index][1:], new_coords[index+1][1:]) # vector between c=c
        # perpendicular_vector = [calculate_vector(coords1[index][1:], coords1[k][1:]) for k in range(num_atoms1) if atom_distance(coords1[index][1:], coords1[k][1:]) < 1.52]

        for k in range(num_atoms1):
            if atom_distance(coords1[index][1:], coords1[k][1:]) < 1.52:
                perpendicular_vector = calculate_vector(coords1[index][1:], coords1[k][1:])
                break
            else: perpendicular_vector = np.array([0,0,1])

        new_coords[index][1:] -= direction * elongation_factor/2 # standard is 10% increase in bond length
        new_coords[index+1][1:] += direction * elongation_factor/2

        perpendicular_axis = np.cross(direction, perpendicular_vector)
        perpendicular_axis = normalize_vector(perpendicular_axis)

        o_index = get_terminal_O(coords2)

        first_pos = new_coords[index][1:] + perpendicular_axis * distance

        rotation_axis = normalize_vector(np.cross(direction, perpendicular_axis))

        # note: fix the rotation axis
        for i in range(num_atoms2):
            relative_vector = calculate_vector(coords2[o_index][1:], coords2[i][1:])
            dot = np.dot(perpendicular_axis, normalize_vector(relative_vector))
            if dot > 0:
                 rotation_angle = np.radians(90) # radians value should be function of dot product
                 rotated_vector = rotate_vector(relative_vector, rotation_axis, rotation_angle)
            else:
                rotation_angle = np.radians(-270)
                rotated_vector = rotate_vector(relative_vector, rotation_axis, rotation_angle)
            new_pos = first_pos + relative_vector + (rotated_vector * 0.1)
            new_coords.append([coords2[i][0], *new_pos])
        

        base_file_name1 = os.path.splitext(os.path.basename(file1))[0]
        base_file_name2 = os.path.splitext(os.path.basename(file2))[0]      
        write_xyz_file(f"{base_file_name1}_{base_file_name2}_{count}.xyz", new_coords)
        count += 1

                        

##################################thermochemistry##############################

def partition_function(vibrations: list, rot_constants, symmetry_num, mol_mass, multiplicity, T, scaling_factor=0.96):
    # vibrational partition function
    qvib = 1 
    for vib in vibrations:
        vib = float(vib)
        if vib > 0:
            qvib *= 1 / (1 - np.exp(-(h * c * vib) / (k_b * T))) 

    # rotational partition function
    ghztowave = 29.978869
    rot_constants = [float(i) * ghztowave for i in rot_constants]
    if 0.0 in rot_constants:
        rot_constant = [e for e in rot_constants if e != 0.0]  
        qrot = ((k_b*T)/(int(symmetry_num)*h*c*rot_constant[0]))
    else:
        qrot = (1/int(symmetry_num)) * ((k_b*T)/(h*c))**1.5 * (np.pi / (rot_constants[0] * rot_constants[1] * rot_constants[2]))**0.5


    # translational partition function
    mol_mass = float(mol_mass) * 1.6605*(10**(-27))
    lmbda = h/(np.sqrt(2*np.pi*mol_mass*k_b*T))
    qtrans = 0.02479/(lmbda**3)

    qelec = float(multiplicity)

    q = qvib*qrot*qtrans*qelec

    return q


def rate_constant(files, t=293.15, program=None):
    reactants = []
    transition_states = []
    
    for file in files:
        dic = {}
        with open (file, "r") as f:
            content = f.read()
            job_info = re.search(r"#(.*)", content)
            job_info = re.split('[, ()]', job_info.group(0)) 
            ee_zpe = re.search(r"sum of electronic and zero-point energies=\s+([-+]?\d*\.\d+|\d+)", content)
            if ee_zpe:
                vibration = re.findall(r"frequencies --\s+(-?\d+\.\d+)", content)
                rot_constant = re.findall(r"rotational constants \(ghz\):\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", content)
                rot_constant = rot_constant[-1]
                symmetry_num = re.search(r"rotational symmetry number\s*(\d+)", content)
                if symmetry_num:
                    symmetry_num = symmetry_num.group(1)
                else: 
                    print("no symmetry number found. assuming 1")
                    symmetry_num = 1
                mol_mass = re.search(r"molecular mass:\s+(-?\d+\.\d+)", content).group(1)
                multiplicity = re.search(r"multiplicity =\s*(\d+)", content).group(1)


                q = partition_function(vibration, rot_constant, symmetry_num, mol_mass, multiplicity, t)

                dic[file] = ""
                dic["ee_zpe"] = float(ee_zpe.group(1)) 
                dic["q"] = q 


                if 'ts' in job_info:
                    transition_states.append(dic)
                else:
                    reactants.append(dic)


            else:
                print(f"no energies in {file}. check if calculation has converged")


    lowest_zpec_ts = min(transition_states, key=lambda x: x['ee_zpe'])['ee_zpe']
    # lowest_zpec_r = min(reactants, key=lambda x: x['ee_zpe'])['ee_zpe']
    
    htoj = 4.3597447222*(10**(-18))
    sum_ts = sum([np.exp((lowest_zpec_ts*htoj - transition_states[i]['ee_zpe']*htoj) / (k_b*t)) *transition_states[i]['q'] for i in range(len(transition_states))])
    # sum_r = [np.exp((lowest_zpec_r*htoj - reactants[i]['ee_zpe']*htoj) / (k_b*t)) *reactants[i]['q'] for i in range(len(transition_states))]
    print(sum_ts)


def main():
    parser = argparse.ArgumentParser(description='''    'Dynamic Approach for Transition State'
Automated tool for generating input files, primarily 
for transition state geometry optimization. 
Calculation of tunneling corrected multi-configurational 
rate constants can also be calculated from log files.''',
                                     prog="JKTS",
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog='''
Examples of use:
                JKTS file.xyz -H -ORCA -method R2SCAN-3C
                JKTS *.xyz -basis def2-TZVPPD --no-xyz
                JKTS reactant.log product.log -k
                                     ''')


    parser.add_argument('input_files', metavar='reactant.xyz', nargs='+', help='XYZ files (e.g., pinonaldehyde.xyz or even *.xyz)')

    reaction_options = parser.add_argument_group("Types of reactions")

    reaction_options.add_argument('-CC', action='store_true', help='Perform addition to C=C bonds')
    reaction_options.add_argument('-H', action='store_true', help='Perform H abstraction with OH radical')
    reaction_options.add_argument('-OH', action='store_true', help='Perform OH addition to C=C bonds')

    parser.add_argument('-G16', action='store_true', help='Create G16 input file')
    parser.add_argument('-ORCA', action='store_true', help='Create ORCA input file')
    parser.add_argument('-crest', action='store_true', help='Create file for CREST constrain')
    parser.add_argument('-constrain', action='store_true', help='Constrain is integrated into input file')

    additional_options = parser.add_argument_group("Additional arguments")

    additional_options.add_argument('--no-TS',action='store_false', default=True, help='Input files for normal geometry relaxation are generated')
    additional_options.add_argument('--no-xyz',action='store_true', help='No XYZ files generated')
    additional_options.add_argument('-k', action='store_true', help='Write me later')
    additional_options.add_argument('-method', nargs="?", default='wb97xd',  help='Specify the QC method [def = wB97X-D]')
    additional_options.add_argument('-basis',  nargs='?', default="6-31+g(d,p)", help='Specify the basis set used [def = 6-31+G(d,p)]')
    additional_options.add_argument('-cpu', nargs='?', const=1, type=int, default=4, help='CPU amount [def = 4]')
    additional_options.add_argument('-mem', nargs='?', const=1, default="4GB", help='Amount of memory allocated for job [def = 4GB]')
    additional_options.add_argument('-par', nargs='?', const=1, default="qany", help='Partition to use [def = qany]')

    global args
    args = parser.parse_args()

    ################################ARGUMENT SPECIFICATIONS############################
    # Program and method 
    global program
    if args.G16:
        program = "G16"
        if args.method == None:
            args.method = "wb97xd"
    elif args.ORCA:
        program = "ORCA"
        if args.method == None:
            args.method = "wB97X-D3"
    else: 
        program = None # Just produce XYZ-file



    for n, input_file in enumerate(args.input_files):
        file_type = input_file.split(".")[1]
        reactant_folder(input_file) # Temp solution
        if file_type == "pkl":
            file_name = input_file.split(".")[0]
            coordinates = pkl_to_xyz(input_file)
            for i, coord in enumerate(coordinates):
                if args.H:
                    H_abstraction(input_file, constrain=args.constrain, TS=args.no_TS, program=program, method=args.method, basis_set=args.basis, no_xyz=args.no_xyz, crest=args.crest)
                if program != None:
                    QC_input(file_path=f"{file_name.replace('collection','')}conf{i+1}", coords=coord, TS=args.no_TS, constrain=args.constrain, program=program, method="B3LYP", basis_set=args.basis)
        
        elif file_type == "xyz":
            if len(args.input_files) == 2 and args.CC:
                addition(args.input_files[n], args.input_files[n+1], constrain=args.constrain, program=program, method=args.method, TS=args.no_TS, no_xyz=args.no_xyz, basis_set=args.basis)
                break
            elif len(args.input_files) > 0 and args.H:
                H_abstraction(input_file, constrain=args.constrain, TS=args.no_TS, program=program, method=args.method, basis_set=args.basis, no_xyz=args.no_xyz, crest=args.crest)
            elif len(args.input_files) > 0 and args.H==False and args.CC==False:
                coords =  read_xyz_file(input_file)
                QC_input(file_path=input_file.split(".")[0], coords=coords, constrain=args.constrain, TS=args.no_TS, program=program, method=args.method, basis_set=args.basis)
        
        elif file_type == "log" and args.k:
            rate_constant(args.input_files)
            break

        else:
            parser.error("Invalid type of input file")


if __name__ == "__main__":
    main()

