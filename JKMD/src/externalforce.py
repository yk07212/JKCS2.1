class ExternalForce:

    def __init__(self, QEF, QEF_par, QEF_systems, towards_point=[0,0,0]):
        if QEF == 'h_A':
          self.qef = 'h_A'
          self.k_ext = QEF_par*0.0433634
          self.end = towards_point
          self.mfrom = QEF_systems[0]
          self.mto = QEF_systems[1]
        if QEF == 'c_COM':
          self.qef = 'c_COM'
          self.force = np.array(QEF_par)*0.0433634
          self.mfrom = QEF_systems[0]
          self.mto = QEF_systems[1]

    def adjust_positions(self, atoms, new):
        pass

    def index_shuffle(self, atoms, ind):
        pass

    def adjust_forces(self, atoms, forces):
        if self.qef == 'h_A':
          import numpy as np
          pos = atoms[self.mfrom:self.mto].get_positions()
          external_force = 2*self.k_ext*np.array([np.linalg.norm(i)*(np.array([0,0,0])-i)/(np.linalg.norm(np.array([0,0,0])-i)+1e-8) for i in pos])
        elif self.qef == 'c_COM':
          import numpy as np
          masses = atoms[self.mfrom:self.mto].get_masses()
          external_force = np.array(masses)[:,np.newaxis]/np.sum(masses) * self.force
        forces[self.mfrom:self.mto] += external_force

    def adjust_potential_energy(self, atoms):
        if self.qef == 'h_A':
          import numpy as np
          pos = atoms[self.mfrom:self.mto].get_positions()
          external_energy = np.sum(self.k_ext*np.array([np.linalg.norm(np.array([0,0,0])-i)**2 for i in pos]))
          return external_energy
        elif self.qef == 'c_COM':
          return 0

    #def todict(self):
    #    return {'name': 'ExternalForce',
    #            'kwargs': {'a1': self.indices[0], 'a2': self.indices[1],
    #                       'f_ext': self.external_force}}
