import class_Quant_NBody
import Quant_NBody
import numpy as np
import matplotlib.pyplot as plt
import scipy.optimize
from essentials import OUTPUT_SEPARATOR, OUTPUT_FORMATTING_NUMBER, print_matrix

def generate_1rdm(Ns, Ne, wave_function):
    # generation of 1RDM
    if Ne % 2 != 0:
        raise f'problem with number of electrons!! Ne = {Ne}, Ns = {Ns}'

    y = np.zeros((Ns, Ns), dtype=np.float64)  # reset gamma
    for k in range(int(Ne / 2)):  # go through all orbitals that are occupied!
        vec_i = wave_function[:, k][np.newaxis]
        y += vec_i.T @ vec_i  #
    return y

def log_calculate_ks_decorator(func):
    def wrapper_func(self):
        ret_val = func(self)

        self.report_string += f"\tEntered calculate_ks\n"

        self.report_string += f"\t\tHartree exchange correlation chemical potential\n\t\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.mu_hxc]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1) + '\n'

        self.report_string += "\t\tmu_s\n\t\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.mu_s]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1)

        temp1 = print_matrix(self.h_ks, ret=True).replace('\n', '\n\t\t')[:-2]
        self.report_string += f'\n\t\th_ks\n\t\t' + temp1 + '\t\t----Diagonalization---'

        temp1 = print_matrix(self.wf_ks, ret=True).replace('\n', '\n\t\t')[:-2]
        self.report_string += f'\n\t\tKS wave function\n\t\t' + temp1

        self.report_string += "\t\tKohn Sham energy\n\t\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.epsilon_s]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1)

        temp1 = print_matrix(self.y_a, ret=True).replace('\n', '\n\t\t')[:-2]
        self.report_string += f'\n\t\t1RDM per spin\n\t\t' + temp1

        self.report_string += "\t\tKS density\n\t\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.n_ks]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1) + '\n'
        return ret_val
    return wrapper_func

def log_add_parameters_decorator(func):

    def wrapper_func(self, u, t, v_ext, equiv_atom_group_list):
        ret_val = func(self, u, t, v_ext, equiv_atom_group_list)
        self.report_string += f"add_parameters:\n\tU_iiii\n\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.u]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1)
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.v_ext]
        self.report_string += f'\n\tv_ext\n\t' + f'{OUTPUT_SEPARATOR}'.join(temp1) + "\n"
        temp1 = print_matrix(self.t, ret=True).replace('\n', '\n\t')[:-1]
        self.report_string += f'\n\tt\n\t' + temp1
        return ret_val
    return wrapper_func

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

        # KS
        self.mu_s = np.zeros(self.Ns, dtype=np.float64)  # Kohn-Sham potential
        self.h_ks = np.array((), dtype=np.float64)
        self.wf_ks = np.array((), dtype=np.float64)  # Kohn-Sham wavefunciton
        self.epsilon_s = np.array((), dtype=np.float64)  # Kohn-Sham energies
        self.n_ks = np.array((), dtype=np.float64)  # Densities of KS sysyem
        self.mu_hxc = np.zeros(self.Ns, dtype=np.float64)  # Hartree exchange correlation potential

        # Quant_NBody objects
        # self.whole_mol = class_Quant_NBody.QuantNBody(self.Ns, self.Ne)
        self.embedded_mol = class_Quant_NBody.QuantNBody(2, 2)
        self.embedded_mol.build_operator_a_dagger_a()

        self.report_string = f'Object with {self.Ns} sites and {self.Ne} electrons\n'

    @log_add_parameters_decorator
    def add_parameters(self, u, t, v_ext, equiv_atom_group_list):
        if len(u) != self.Ns or len(t) != self.Ns or len(v_ext) != self.Ns:
            raise f"Problem with size of matrices: U={len(u)}, t={len(t)}, v_ext={len(v_ext)}"
        self.u = u
        self.t = t
        self.v_ext = v_ext
        for index, item in enumerate(equiv_atom_group_list):
            self.equiv_atom_groups[index] = tuple(item)

    @log_calculate_ks_decorator
    def calculate_ks(self):
        self.mu_s = self.mu_hxc - self.v_ext
        self.h_ks = self.t - np.diag(self.mu_s)
        self.epsilon_s, self.wf_ks = np.linalg.eigh(self.h_ks, 'U')
        self.y_a = generate_1rdm(self.Ns, self.Ne, self.wf_ks)
        self.n_ks = np.copy(self.y_a.diagonal())

    def log_CASCI(self, site_id, y_a_correct_imp, P, v, h_tilde, h_tilde_dimer):
        self.report_string += f'\t\t\tNew 1RDM that is optained by replacing indices 0 and {site_id}\n\t\t\t'
        temp1 = print_matrix(y_a_correct_imp, ret=True).replace('\n', '\n\t\t\t')[:-3]
        self.report_string += temp1

        self.report_string += "\t\t\tv vector\n\t\t\t"
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in v[:, 0]]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1) + "\n"

        self.report_string += f'\t\t\tP matrix\n\t\t\t'
        temp1 = print_matrix(P, ret=True).replace('\n', '\n\t\t\t')[:-3]
        self.report_string += temp1

        self.report_string += f'\t\t\th tilde\n\t\t\t'
        temp1 = print_matrix(h_tilde, ret=True).replace('\n', '\n\t\t\t')[:-3]
        self.report_string += temp1

        self.report_string += f'\t\t\th tilde dimer\n\t\t\t'
        temp1 = print_matrix(h_tilde_dimer, ret=True).replace('\n', '\n\t\t\t')[:-3]
        self.report_string += temp1

        self.report_string += f'\t\t\tU0 parameter: {self.u[site_id]}\n'
        self.report_string += f'\t\t\tStarting impurity chemical potential mu_imp: {self.mu_hxc[site_id]}\n'

    def log_scl(self, old_density, mean_square_difference_density, i, tolerance, num_iter):
        self.report_string += f"\tNew densities: "
        temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in self.n_ks]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1) + "\n"
        self.report_string += f"\tOld densities: "
        if type(old_density) == float:
            temp1 = ["inf"]
        else:
            temp1 = ['{num:{dec}}'.format(num=cell, dec=OUTPUT_FORMATTING_NUMBER) for cell in old_density]
        self.report_string += f'{OUTPUT_SEPARATOR}'.join(temp1) + "\n"
        self.report_string += f"\tO: average square difference: {mean_square_difference_density}\n\tO:Stopping" \
                              f" condtition: {mean_square_difference_density}<{tolerance} OR {i}>={num_iter}-1"

    def CASCI(self):
        self.report_string += "\tEntered CASCI\n"
        for site_group in self.equiv_atom_groups.keys():
            self.report_string += f"\t\tGroup {site_group} with sites {self.equiv_atom_groups[site_group]}\n"
            site_id = self.equiv_atom_groups[site_group][0]

            # Householder transforms impurity on index 0 so we have to make sure that impurity is on index 0:
            if site_id == 0:
                y_a_correct_imp = np.copy(self.y_a)
            else:
                # We have to move impurity on the index 0
                y_a_correct_imp = np.copy(self.y_a)
                y_a_correct_imp[:, [0, site_id]] = y_a_correct_imp[:, [site_id, 0]]
                y_a_correct_imp[[0, site_id], :] = y_a_correct_imp[[site_id, 0], :]

            P, v = Quant_NBody.Householder_transformation(y_a_correct_imp)
            h_tilde = P @ (self.t + np.diag(self.v_ext)) @ P
            # TODO: Ask Fromager how should this be!!!

            h_tilde_dimer = h_tilde[:2, :2]
            u_0_dimer = np.zeros((2, 2, 2, 2), dtype=np.float64)
            u_0_dimer[0,0,0,0] += self.u[site_id]
            # h_tilde_dimer[0,0] += self.v_ext[site_id]
            mu_imp = self.mu_hxc[site_id]

            self.log_CASCI(site_id, y_a_correct_imp, P, v, h_tilde, h_tilde_dimer)

            optimized_v_imp_obj = scipy.optimize.minimize(cost_function_CASCI,
                                                          np.array([mu_imp]),
                                                          args=(self.embedded_mol, h_tilde_dimer, u_0_dimer,
                                                                self.n_ks[site_id]),
                                                          method='BFGS', tol=1e-2)
            # This minimize cost function (difference between KS occupations and CASCI occupations squared)
            error = optimized_v_imp_obj['fun']
            mu_imp = optimized_v_imp_obj['x'][0]

            self.report_string += f'\t\t\tOptimized chemical potential mu_imp: {mu_imp}\n'
            self.report_string += f'\t\t\tError in densities (square): {error}\n'

            print(f"managed to get E^2={error} with mu_imp={mu_imp}")
            for every_site_id in self.equiv_atom_groups[site_group]:
                self.mu_hxc[every_site_id] = mu_imp

    def self_consistent_loop(self, num_iter=10, tolerance=0.0):
        self.report_string += "self_consistent_loop:\n"
        old_density = np.inf
        densities = []
        for i in range(num_iter):
            self.report_string += f"Iteration # = {i}\n"
            self.calculate_ks()
            densities.append(self.n_ks.copy())
            self.CASCI()
            print(f"Loop {i}-----")
            print("\tdensities:", self.n_ks)
            print('\tnew Hxc potentials: ', self.mu_hxc)
            mean_square_difference_density = np.average(np.square(self.n_ks - old_density))

            self.log_scl(old_density, mean_square_difference_density, i, tolerance, num_iter)

            if mean_square_difference_density < tolerance:
                break
            old_density = self.n_ks
        densities = np.array(densities)
        for i in range(densities.shape[1]):
            plt.plot(densities[:,i])
        plt.show()


    def compare_densities_FCI(self):
        mol_full = class_Quant_NBody.QuantNBody(self.Ns, self.Ne)
        mol_full.build_operator_a_dagger_a()
        u_4d = np.zeros((self.Ns, self.Ns, self.Ns, self.Ns))
        for i in range(self.Ns):
            u_4d[i,i,i,i] = self.u[i]
        mol_full.build_hamiltonian_fermi_hubbard(self.t + np.diag(self.v_ext), u_4d)
        mol_full.diagonalize_hamiltonian()
        y_ab = mol_full.calculate_1RDM_tot()
        print("FCI densities (per spin):", y_ab.diagonal()/2)
        return y_ab, mol_full


def cost_function_CASCI(mu_imp, embedded_mol, h_tilde_dimer, u_0_dimer, desired_density):
    mu_imp = mu_imp[0]
    mu_imp_array = np.array([[mu_imp, 0], [0, 0]])
    embedded_mol.build_hamiltonian_fermi_hubbard(h_tilde_dimer + mu_imp_array, u_0_dimer)
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

def ring_abab_calculate_ks_difference_1rdm():
    mol1 = Molecule(4, 4)
    x = []
    y1 = []
    y2 = []
    for pmv in np.arange(0, 3, 0.1):
        t, v_ext, u = generate_from_graph(
            {0: {'v': -pmv, 'U': 1}, 1: {'v': pmv, 'U': 1}, 2: {'v': -pmv, 'U': 1}, 3: {'v': pmv, 'U': 1}},
            {(0, 1): 1, (1, 2): 1, (2, 3): 1, (0, 3): 1})
        mol1.add_parameters(u, t, v_ext, [[0, 2], [1, 3]])
        mol1.calculate_ks()
        x.append(pmv)
        y1.append(mol1.n_ks[0])
        y2.append(mol1.n_ks[1])
    plt.plot(x, y1)
    plt.plot(x, y2)
    plt.show()
    # Problem that 4 site with 4 electron is still degenerated (I think!)



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
    mol1 = Molecule(6, 6)
    pmv = 0.1
    t, v_ext, u = generate_from_graph({0:{'v':-pmv, 'U':1}, 1:{'v':pmv, 'U':1}, 2:{'v':pmv, 'U':1}, 3:{'v':-pmv, 'U':1},
                                       4:{'v':pmv, 'U':1}, 5:{'v':pmv, 'U':1}},
                                      {(0, 1):1, (1, 2):1, (2, 3):1, (3,4):1, (4,5):1, (0,5):1})
    mol1.add_parameters(u, t, v_ext, [[0, 3], [1, 2, 4, 5]])
    mol1.self_consistent_loop()
    print(mol1.report_string)
    y_real, mol_full = mol1.compare_densities_FCI()