import class_Quant_NBody
import Quant_NBody
import numpy as np
import matplotlib.pyplot as plt
from single_shot import print_matrix
import scipy.optimize

def generate_1rdm(Ns, Ne, wave_function):
    # generation of 1RDM
    if Ne % 2 != 0:
        raise f'problem with number of electrons!! Ne = {Ne}, Ns = {Ns}'

    y = np.zeros((Ns, Ns), dtype=np.float64)  # reset gamma
    for k in range(int(Ne / 2)):  # go through all orbitals that are occupied!
        vec_i = wave_function[:, k][np.newaxis]
        y += vec_i.T @ vec_i  #
    return y


class Molecule:
    def __init__(self, site_number, electron_number):
        # Basic data about system
        self.Ns = site_number
        self.Ne = electron_number

        # Parameters for the system
        self.u = np.array((), dtype=np.float64)  # for the start only 1D array and not 4d tensor
        self.t = np.array((), dtype=np.float64)  # 2D one electron Hamiltonian with all terms for i != j
        self.v_ext = np.array((), dtype=np.float64)  # 1D one electron Hamiltonian with all terms for i == j
        self.equiv_atom_groups = dict()

        # density matrix
        # TODO: What do I actially need?
        self.y_a = np.array((), dtype=np.float64)  # y --> gamma, a --> alpha so this indicates it is only per one spin
        self.y_a_tilde = np.array((), dtype=np.float64)  # density matrix in Householder transformed space
        self.occupation_CASCI = np.zeros(self.Ns, dtype=np.float64)

        # KS
        self.mu_s = np.zeros(self.Ns, dtype=np.float64)  # Kohn-Sham potential
        self.h_ks = np.array((), dtype=np.float64)
        self.wf_ks = np.array((), dtype=np.float64)  # Kohn-Sham wavefunciton
        self.epsilon_s = np.array((), dtype=np.float64)  # Kohn-Sham energies
        self.n_ks = np.array((), dtype=np.float64)  # Densities of KS sysyem

        # Hxc potential TODO: initializaiton with another method?
        self.mu_hxc = np.zeros(self.Ns, dtype=np.float64)  # Hartree exchange correlation potential

        # Quant_NBody objects
        # self.whole_mol = class_Quant_NBody.QuantNBody(self.Ns, self.Ne)
        self.embedded_mol = class_Quant_NBody.QuantNBody(2, 2)
        self.embedded_mol.build_operator_a_dagger_a()

        # Householder
        self.P = np.array((), dtype=np.float64)
        self.v = np.array((), dtype=np.float64)

    def add_parameters(self, u, t, v_ext, equiv_atom_group_list):
        if len(u) != self.Ns or len(t) != self.Ns or len(v_ext) != self.Ns:
            raise f"Problem with size of matrices: U={len(u)}, t={len(t)}, v_ext={len(v_ext)}"
        self.u = u
        self.t = t
        self.v_ext = v_ext
        for index, item in enumerate(equiv_atom_group_list):
            self.equiv_atom_groups[index] = tuple(item)

    def calculate_ks(self):
        self.mu_s = self.mu_hxc - self.v_ext
        self.h_ks = self.t - np.diag(self.mu_s)
        self.epsilon_s, self.wf_ks = np.linalg.eigh(self.h_ks, 'U')
        self.y_a = generate_1rdm(self.Ns, self.Ne, self.wf_ks)
        self.n_ks = self.y_a.diagonal()

    def CASCI(self):
        for site_group in self.equiv_atom_groups.keys():
            site_id = self.equiv_atom_groups[site_group][0]

            # Householder transforms impurity on index 0 so we have to make sure that impurity is on index 0:
            if site_id == 0:
                y_a_correct_imp = self.y_a
            else:
                # We have to move impurity on the index 0
                y_a_correct_imp = self.y_a
                y_a_correct_imp[:, [0, site_id]] = y_a_correct_imp[:, [site_id, 0]]

            P, v = Quant_NBody.Householder_transformation(y_a_correct_imp)

            y_a_tilde = P @ y_a_correct_imp @ P

            h_tilde = np.einsum('ki, ij, jl -> kl', P, y_a_tilde, P)  # equivalent to P @ y_a_tilde @ P

            h_tilde_dimer = h_tilde[:2, :2]
            u_0_dimer = np.zeros((2, 2, 2, 2), dtype=np.float64)
            u_0_dimer[0,0,0,0] += self.u[site_id]
            h_tilde_dimer[0,0] -= self.v_ext[site_id]
            v_imp = self.mu_hxc[site_id]
            optimized_v_imp_obj = scipy.optimize.minimize(cost_function_CASCI,
                                                          np.array([v_imp]),
                                                          args=(self.embedded_mol, h_tilde_dimer, u_0_dimer,
                                                                self.n_ks[site_id]),
                                                          method='BFGS', tol=1e-2)
            # This minimize cost function (difference between KS occupations and CASCI occupations squared)
            error = optimized_v_imp_obj['fun']
            v_imp = optimized_v_imp_obj['x'][0]
            print(f"managed to get E^2={error} with v_imp={v_imp}")
            for every_site_id in self.equiv_atom_groups[site_group]:
                self.mu_hxc[every_site_id] = v_imp

    def self_consistent_loop(self, num_iter=10, tolerance=0.01):
        old_density = np.inf
        for i in range(num_iter):
            self.calculate_ks()
            print(self.epsilon_s, '-----\ndensity_matrix')
            print_matrix(self.y_a)
            print(self.n_ks)
            self.CASCI()
            print(f"Loop {i}-----")
            print("\tdensities:", self.n_ks)
            print('\tnew Hxc potentials: ', self.mu_hxc)
            mean_square_difference_density = np.average(np.square(self.n_ks - old_density))
            if mean_square_difference_density < tolerance:
                break
            old_density = self.n_ks



def cost_function_CASCI(v_imp, embedded_mol, h_tilde_dimer, u_0_dimer, desired_density):
    v_imp = v_imp[0]
    embedded_mol.build_hamiltonian_fermi_hubbard(h_tilde_dimer+np.array([[-v_imp, 0], [0, 0]]), u_0_dimer)
    embedded_mol.diagonalize_hamiltonian()

    density_dimer = Quant_NBody.Build_One_RDM_alpha(embedded_mol.WFT_0, embedded_mol.a_dagger_a)
    return (density_dimer[0, 0] - desired_density) ** 2

def generate_from_graph(sites, connections):
    """
    We can provide graph information and program generates hamiltonian automatically
    :param sites: in the type of: {0:{'v':0, 'U':4}, 1:{'v':1, 'U':4}, 2:{'v':0, 'U':4}, 3:{'v':1, 'U':4}}
    :param connections: {(0, 1):1, (1, 2):1, (2, 3):1, (0,3):1}
    :return: h and U parameters
    """
    n_sites = len(sites)
    t = np.zeros((n_sites, n_sites), dtype=np.float64)
    v = np.zeros(n_sites, dtype=np.float64)
    u = np.zeros(n_sites, dtype=np.float64)
    for id, params in sites.items():
        if 'U' in params:
            u[id] = params['U']
        elif 'u' in params:
            u[id] = params['u']
        else:
            raise "Problem with params: " + params
        v[id] = params['v']
    for pair, param in connections.items():
        t[pair[0], pair[1]] = -param
        t[pair[1], pair[0]] = -param
    return t, v, u

if __name__ == "__main__":
    # circle = Molecule(6, 6)
    # Ns = 6
    # number_of_electrons = 6
    # # region generation of ring
    # h = np.zeros((Ns, Ns), dtype=np.float64)  # reinitialization
    # t = 1
    # if (number_of_electrons / 2) % 2 == 0:
    #     h[0, Ns - 1] = t
    #     h[Ns - 1, 0] = t
    #     pl = "ANTIPERIODIC" + '\n'
    # else:
    #     h[0, Ns - 1] = -t
    #     h[Ns - 1, 0] = -t
    #     pl = "PERIODIC" + '\n'
    #
    # h += np.diag(np.full((Ns - 1), -t), -1) + np.diag(np.full((Ns - 1), -t), 1)
    # u = np.zeros(Ns) + 4
    # v_ext = np.zeros(Ns)
    # eq_sites = [list(range(6))]
    # # endregion
    # circle.add_parameters(u, h, v_ext, eq_sites)
    # circle.calculate_ks()
    # circle.CASCI()
    mol1 = Molecule(4, 4)
    t, v_ext, u = generate_from_graph({0:{'v':-1, 'U':4}, 1:{'v':1, 'U':4}, 2:{'v':-1, 'U':4}, 3:{'v':1, 'U':4}},
                                      {(0, 1):1, (1, 2):1, (2, 3):1, (0,3):1})
    mol1.add_parameters(u, t, v_ext, [[0, 2], [1, 3]])
    mol1.self_consistent_loop()