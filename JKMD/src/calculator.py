def calculator(Qcalculator, Qcalculator_input, Qcalculator_max_iterations, Qcharge, Qout, atoms):

# TODO
### 792 /home/kubeckaj/Applications/JKCS2.1/JKQC/JKCS/lib/python3.9/site-packages/ase/calculators/calculator.py
#        if atoms is not None:
#            CS = atoms.constraints
#            del atoms.constraints
#            self.atoms = atoms.copy()
#            atoms.set_constraint(CS)
  if Qout > 0:
    Qprint=Qout-1
  else:
    Qprint=Qout
   
  ### XTB-Lite ###
  if Qcalculator == "XTB1":
    #if Qcharge != 0:
    #  print("Oh sorry, the charge has not been implemented yet, ask Jakub.")
    #  exit()
    from tblite.ase import TBLite
    return TBLite(method="GFN1-xTB", charge=float(Qcharge), verbosity = Qprint, max_iterations = Qcalculator_max_iterations, accuracy = 1.0)

  elif Qcalculator == "XTB2":
    #if Qcharge != 0:
    #  print("Oh sorry, the charge has not been implemented yet, ask Jakub.")
    #  exit()
    from tblite.ase import TBLite
    return TBLite(method="GFN2-xTB", charge=float(Qcharge), verbosity = Qprint, max_iterations = Qcalculator_max_iterations, accuracy = 1.0)

  ### XTB ###
  elif Qcalculator == "XTB":
    if Qcharge != 0:
      print("Oh sorry, the charge has not been implemented yet, ask Jakub.")
      exit()
    from xtb.ase.calculator import XTB
    return XTB(method=Qcalculator_input);#+" --chrg "+str(Qcharge))#, charge=Qcharge)

  ### ORCA ###
  elif Qcalculator == "ORCA":
    from ase.calculators.orca import ORCA
    import psutil
    import os
    #os.system("if ! command -v module &> /dev/null; then source /com/bin/modules.sh; fi; module load intel; module load openmpi;")
    #os.system("if ! command -v module &> /dev/null; then source /com/bin/modules.sh; fi; module load gcc openmpi mkl")
    #os.environ['OMP_STACKSIZE'] = '4G'
    #os.environ['OMP_NUM_THREADS'] = f'{len(psutil.Process().cpu_affinity())}'
                               #f'{len(psutil.Process().cpu_affinity())},1'
    #os.environ['OMP_MAX_ACTIVE_LEVELS'] = '1'
    cpus = f'{len(psutil.Process().cpu_affinity())}'
    return ORCA(charge=Qcharge,
                mult=1,
                orcasimpleinput=Qcalculator_input,
                orcablocks='%pal nprocs '+cpus+' end')

  ### SchNetPack ###
  elif Qcalculator == "NN":
    from schnetpack.interfaces import SpkCalculator
    import schnetpack as spk
    return SpkCalculator(
        #model_file="/home/kubeckaj/ML_TEST/NN2/T5_MD/T3_RPMDaLAN/model.pkl",
        #model_file="model.pkl",
        #model_file="/home/kubeckaj/PROJECT_NN/MORTEN_SA_AM_CORRECT/TRAIN_FULL_42_i400/model.pkl",
        #model_file="/home/kubeckaj/ML_TEST/NN2/T6_understandin_1k/T06_FINAL_TRAINING/TRAIN_FULL_42_i400/model.pkl",
        #model_file="/home/kubeckaj/ML_TEST/NN2/T7_CCSDT/TRAIN_ML_0-5w/RUN7/model.pkl",
        #model_file="/home/kubeckaj/ML_TEST/NN2/XTB/ML-TRAIN/model.pkl",
        #model_file="/home/kubeckaj/ML_TEST/NN2/T7_CCSDT/TRAIN_ML_0-1sa0-6w/model.pkl",
        #model_file="/home/kubeckaj/ML_TEST/NN2/T6_understandin_1k/T06_FINAL_TRAINING/TRAIN_FULL_42_B97-3c_i400/model.pkl",
        model_file = Qcalculator_input,
        device="cpu",
        #neighbor_list=spk.transform.ASENeighborList(cutoff=10.0),
        neighbor_list=spk.transform.TorchNeighborList(cutoff=5.0), #,cutoff_shell=2.0),
        #transforms=spk.transform.atomistic.SubtractCenterOfMass(),
        energy_key='energy',
        force_key='forces',
        energy_unit="eV",#not sure about units --- maybe Hartree
        force_unit="eV/Ang",#not sure about units
        position_unit="Ang",
        )

  ### PhysNet ###
  elif Qcalculator == "PhysNet":
    import os,sys
    pathname = os.path.dirname(sys.argv[0])
    sys.path.append(pathname + "/../JKML/src/PhysNet_DER/")
    from PNcalculator import PhysNetCalculator  
   
    from torch import cuda
    if cuda.is_available():
      os.system("sed -i 's/--device=cpu/--device=cuda/g' input.inp")
    else:
      os.system("sed -i 's/--device=cuda/--device=cpu/g' input.inp")
  
    return PhysNetCalculator(
      checkpoint=Qcalculator_input,
      atoms=atoms,
      charge=Qcharge,
      config='input.inp')

  else:
    print("Unknown calculator.")
    exit()
