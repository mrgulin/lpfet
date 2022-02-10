import numpy as np
import single_shot as ss
import Quant_NBody
import LPFET.class_Quant_NBody
import single_shot

embedded_mol = LPFET.class_Quant_NBody.QuantNBody(2, 2)
embedded_mol.build_operator_a_dagger_a()


def CASCI_dimer(gamma, h, u, v_imp):
    # Density in range [0, 2]
    P, v = Quant_NBody.Householder_transformation(gamma)
    y_a_tilde = P @ gamma @ P
    # h_tilde = np.einsum('ki, ij, jl -> kl', P, y_a_tilde, P)
    # print("!!!", np.allclose(P @ y_a_tilde @ P, np.einsum('ki, ij, jl -> kl', P, y_a_tilde, P)))
    h_tilde = P @ h @ P
    # single_shot.print_matrix(P)
    # print('-')
    # single_shot.print_matrix(h)
    # print('-')
    # single_shot.print_matrix(h_tilde)
    h_tilde_dimer = h_tilde[:2, :2]
    u_0_dimer = np.zeros((2, 2, 2, 2), dtype=np.float64)
    u_0_dimer[0, 0, 0, 0] += u
    h_tilde_dimer[0, 0] += v_imp
    print("--------\nh_tilde_dimer")
    single_shot.print_matrix(h_tilde_dimer)

    # Subtract v_hcx
    embedded_mol.build_hamiltonian_fermi_hubbard(h_tilde_dimer, u_0_dimer)
    embedded_mol.diagonalize_hamiltonian()
    density_dimer = Quant_NBody.Build_One_RDM_alpha(embedded_mol.WFT_0, embedded_mol.a_dagger_a)
    # TODO: Change this with some object in class
    density = density_dimer[0, 0] * 2
    print("--------\ndensity")
    single_shot.print_matrix(density_dimer)
    print(embedded_mol.ei_val)
    energy = embedded_mol.ei_val[0]
    return density, energy


class ScHouseholder(ss.Householder):  # Child class!!
    def __init__(self, particle_number: int, u: float, mu_ext: float, bath_interaction='NIB'):
        super().__init__(particle_number=particle_number, electron_number=0, u=u, debug=False, skip_unnecessary=False,
                         bath_interaction=bath_interaction)
        self.mu_ext = mu_ext
        self.iter_num = 0
        self.procedure_log += f"Created object with μ_ext={round(self.mu_ext, 3)}, N={self.N}"
        self.procedure_log += f", U={self.U}, bath={self.bath_interaction}\n"
        print('\n', self.procedure_log[:-1], ": ", end='')

    def self_consistent_loop(self, already_calculated=False):
        self.procedure_log += f"\tEntering self consistent loop. h and eigenvectors are"
        self.procedure_log += f"{'NOT' * already_calculated} calculated from scratch\n"
        if not already_calculated:
            self.procedure_log += '\t\t' + self.generate_huckel_hamiltonian(2)  # 2 for periodic condition
            self.calculate_eigenvectors()

        N_ele = 0
        for i in range(self.N):  # Also size of matrix of 1RDM and ei_val
            if (self.ei_val[i] < self.mu_ext + 1E-10):
                N_ele += 2
        self.Ne = N_ele
        self.n = self.Ne / self.N
        self.procedure_log += f"\t\tbased on mu_ext={self.mu_ext} there are {self.Ne} electrons"
        self.procedure_log += f"and density is {self.n}\n"
        self.generate_1rdm()
        self.generate_householder_vector()
        # self.calculate_variables()
        # self.e_site["main"] = 4.0 * self.t * (1.0 - 2.0 * (self.v[1] ** 2)) * self.vars['hopping'][0] + self.U * \
        #                       self.vars['d_occ'][0]
        print("Density1:", self.vars['density'][0])
        density_ana = self.vars['density'][0]
        self.vars['density'][0], self.e_site["main"] = CASCI_dimer(self.gamma, self.h, self.U, -self.mu_Hxc)
        print("Density2:", self.vars['density'][0], "diffrence:", density_ana - self.vars['density'][0])
        self.procedure_log += f"\t\tEnergy based on filling is {self.e_site['main']}\n"
        self.Ne = self.vars['density'][0] * self.N
        self.n = self.Ne / self.N

    def loops(self, convergence_threshold=1.0e-5, max_iter=20):
        self.procedure_log += f"\tentering loops:\n"
        self.iter_num = 0
        for self.iter_num in range(1, max_iter + 1):
            self.procedure_log += f"\t- iteration {self.iter_num} --------------------------------------\n"
            print(self.iter_num, end=", ")
            conv_test = self.Ne
            # region DENSITY_MATRIX_BIS + SELFCONSISTENTLOOP_BIS
            # No need to calculate eigenvalues one more time if we are doing periodic conditions always!!
            index = electron_number_to_ei_vec_id(self.Ne)
            self.mu_KS = self.ei_val[index]
            self.mu_Hxc = self.mu_ext - self.mu_KS

            self.procedure_log += f"\t\tNelect before loop = {self.Ne}-->index = {index}"
            self.procedure_log += f", KS energy that corresponds to this energy: {self.mu_KS} (this is calculated from"
            self.procedure_log += f" density)\n"
            self.procedure_log += f"\t\tμ_Hxc = μ_KS - μ_ext = {self.mu_Hxc}\n"
            self.procedure_log += f"\t\tBased on this potential we start to build new 1RDM with the correct μ_KS\n"

            N_ele = 0
            for j in range(self.N):  # Also size of matrix of 1RDM and ei_val
                if self.ei_val[j] < self.mu_KS + 1E-10:
                    N_ele += 2

            self.Ne = N_ele
            self.n = self.Ne / self.N
            self.generate_1rdm()
            self.generate_householder_vector()

            self.procedure_log += f"\t\tBased on μ_KS={self.mu_KS} we get Ne={self.Ne} or n={self.n}\n"

            # self.calculate_variables()
            self.e_site["without_mu_opt"] = 4.0 * self.t * (1.0 - 2.0 * (self.v[1] ** 2)) * self.vars['hopping'][0] + \
                                            self.U * self.vars['d_occ'][0]
            # self.e_site["main"] = 4.0 * self.t * (1.0 - 2.0 * (self.v[1] ** 2)) * self.vars['hopping'][0] + self.U * \
            #                       self.vars['d_occ'][0]
            print("Density1:", self.vars['density'][0], '\n', self.vars)
            density_ana = self.vars['density'][0]
            self.vars['density'][0], self.e_site["main"] = CASCI_dimer(self.gamma, self.h, self.U, -self.mu_Hxc)
            print("Density2:", self.vars['density'][0], "diffrence:", density_ana - self.vars['density'][0])
            self.Ne = self.vars['density'][0] * self.N
            self.n = self.Ne / self.N
            conv_test = conv_test - self.Ne

            self.procedure_log += f"\t\tBased on changed HH vector and μ_Hxc we calculate energy and also imp site"
            self.procedure_log += f" occupation = {self.vars['density'][0]}, E_site = {self.e_site['main']}\n"
            self.procedure_log += f"\t\tBased on this occupation we can calculate new density = {self.n} and number"
            self.procedure_log += f" of electrons = {self.Ne}. This are data after loop\n"
            self.procedure_log += f"\t\tAt the end of loop we do convergence test: abs(Ne_start - Ne_end) = {conv_test}"
            self.procedure_log += f" has to be smaller then {convergence_threshold}\n"

            if abs(conv_test) < convergence_threshold:
                self.procedure_log += f"\t\t\tIt is so we break the loop\n"
                break
            # endregion
        self.write_report(False)

    def write_report(self, print_result=True):
        # Result_string part
        # combined_result_string
        columns = ['N', 'Ne', 'density', 'μ_KS', 'μ_Hxc', 'μ_ext', 'μ_xc', 'iter_num', 'site_e']
        values = [self.N, self.Ne, self.n, self.mu_KS, self.mu_Hxc, self.mu_ext, self.mu_Hxc - 0.5 * self.U * self.n,
                  self.iter_num, self.e_site['main']]
        val_str = ''.join([f'{num:{ss.OUTPUT_FORMATTING_NUMBER}}' for num in values])
        self.combined_results_string['row'] = val_str + '\n'
        self.combined_results_string['col_names'] = "".join(
            [f'{i:>{int(len(val_str) / len(columns))}}' for i in columns])
        self.combined_results_string['col_names'] += '\n'
        if print_result:
            print(self.combined_results_string['col_names'])
            print(self.combined_results_string['row'])
        # I put col names and row data into combined_results_string. I used such formatting that columns are aligned


class MetaScHouseholder:
    def __init__(self, number_of_sites, u, min_mu=-2., max_mu=6., step=0.1, t=1):
        self.U = u
        self.N = number_of_sites
        self.h = np.array
        self.range = (min_mu, max_mu + step, step)
        self.t = t
        self.h = np.array([])
        self.ei_vec = np.array([])
        self.ei_val = np.array([])
        a = len(np.arange(*self.range))
        self.common_filename = f'N-{self.N}_U-{self.U}_{a}.dat'

    generate_huckel_hamiltonian = ScHouseholder.generate_huckel_hamiltonian

    calculate_eigenvectors = ScHouseholder.calculate_eigenvectors

    def calculate_many_potentials(self):
        data_string = ''
        result_string = ''
        self.generate_huckel_hamiltonian(2)
        self.calculate_eigenvectors()

        for mu_ext1 in np.arange(*self.range):
            obj2 = ScHouseholder(self.N, self.U, mu_ext1)
            try:
                obj2.h = self.h
                obj2.ei_val = self.ei_val
                obj2.ei_vec = self.ei_vec
                obj2.self_consistent_loop(already_calculated=True)

                obj2.loops()

            except np.linalg.LinAlgError:
                pass
            if result_string == '':
                result_string = obj2.combined_results_string['col_names']
            result_string += obj2.combined_results_string['row']
            data_string += obj2.procedure_log
            data_string += "-" * 20 + '\n'
        data_file = open('procedure_log_sc/Saad_' + self.common_filename, 'w', encoding='UTF-8')
        data_file.write(data_string)
        data_file.close()
        result_file = open(f"results_sc/Saad_" + self.common_filename, 'w', encoding='UTF-8')
        result_file.write(result_string)
        result_file.close()
        print(self.ei_val)


def electron_number_to_ei_vec_id(Ne):
    index = round(Ne)
    index -= 1
    index /= 2
    index = int(np.floor(index))
    if index <= 0:
        index = 0
    return index


if __name__ == '__main__':
    # obj = ScHouseholder(400, 4, 0)
    # obj.self_consistent_loop()
    # obj.loops()
    # obj.write_report()
    # print(obj.procedure_log)
    meta_obj = MetaScHouseholder(6, 4, -0.5, 0.5, 0.01)
    meta_obj.calculate_many_potentials()