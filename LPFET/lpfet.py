from LPFET import essentials
import numpy as np
import matplotlib.pyplot as plt
import scipy.optimize
from datetime import datetime
from sklearn.linear_model import LinearRegression
from typing import Union
import typing
import quantnbody as qnb  # Folder Quant_NBody has to be in the sys.path or installed as package.
import quantnbody_class_new as class_qnb
from LPFET import errors
import logging

# region logging settings
# This is used instead of prints. By setting handler level (between 1 and 50) you can adjust number of messages
# depending on the importance
formatter = logging.Formatter('%(levelname)8s %(lineno)4s %(asctime)s: %(message)s', "%Y-%m-%d %H:%M:%S")
general_handler = logging.FileHandler('general_lpfet.log', mode='w')
general_handler.setFormatter(formatter)
general_handler.setLevel(20)  # <- sets amount that is printed in the log file

stream_handler = logging.StreamHandler()
stream_handler.setLevel(25)  # <- sets amount that is printed on the console

general_logger = logging.getLogger(__name__)
general_logger.setLevel(-1)
general_logger.addHandler(general_handler)
general_logger.addHandler(stream_handler)
# endregion

ROOT_FINDING_LIST_INPUT = []  # All inputs for the cluster correlated wave function calculations
ROOT_FINDING_LIST_OUTPUT = []  # All outputs for the cluster correlated wave function calculations
ITERATION_NUM = 0
ACTIVE_SPACE_CALCULATION = False
BATH_TYPE = "NIB"  # IN or NIB

ROOT_LPFET_SOLVER_MAX_ITER = 100
np.seterr(all='raise')
np.errstate(all='raise')
np.set_printoptions(linewidth=np.inf)  # no breaking of lines when printing np.arrays

ITER_NUM_ROOT = 0
FUN_ERROR_END = 0


def householder_transformation(matrix, fragment_size=1):
    """
    This function calls either single-impurity Householder or block Householder transformation. Also it takes care of
    all possible errors.
    :param matrix: 1RDM that has fragments on first fragment_size places
    :param fragment_size: number of fragments. By default it is 1 (single-impurity transformation)
    :return: P, rest; P is the Householder transformation. In single impurity case the second variable is vector v.
    In block-Householder it is the moore_penrose_inv.
    """
    try:
        if fragment_size == 1:
            p, r = qnb.tools.householder_transformation(matrix)
        else:
            p, r = qnb.tools.block_householder_transformation(matrix, fragment_size)
    except BaseException as e:
        raise errors.HouseholderTransformationError(matrix) from e
    if np.any(np.isnan(p)):
        raise errors.HouseholderTransformationError(matrix)
    return p, r


def get_active_space_integrals_fh_v_term(two_body_integrals,
                                         occupied_indices=None,
                                         active_indices=None):
    occupied_indices = [] if occupied_indices is None else occupied_indices
    if len(active_indices) < 1:
        raise ValueError('Some active indices required for reduction.')

    # Determine core constant
    core_constant = 0.0
    for i in occupied_indices:
        for j in occupied_indices:
            core_constant += 2 * 2 * two_body_integrals[i, i, j, j]
            core_constant += 2 * two_body_integrals[i, j, j, i]

    # Modified one electron integrals
    one_body_integrals_new = np.zeros(two_body_integrals.shape[:2])
    for u in active_indices:
        for i in occupied_indices:
            # one_body_integrals_new += two_body_integrals[i, u, u, i]
            for v in active_indices:
                one_body_integrals_new[u, v] += 2 * two_body_integrals[i, i, u, v]
                one_body_integrals_new[u, v] += 2 * two_body_integrals[u, v, i, i]
                one_body_integrals_new[u, v] -= two_body_integrals[i, v, u, i]

                # Restrict integral ranges and change M appropriately
    return (core_constant,
            one_body_integrals_new[np.ix_(active_indices, active_indices)],
            two_body_integrals[np.ix_(active_indices, active_indices, active_indices, active_indices)])


def fh_get_active_space_integrals(h_, U_, frozen_indices=None, active_indices=None):
    """
    Restricts a Fermi-Hubbard at a spatial orbital level to an active space
    This active space may be defined by a list of active indices and
    doubly occupied indices. Note that one_body_integrals and
    two_body_integrals must be defined in an orthonormal basis set (MO like).
    Args:
         - occupied_indices: A list of spatial orbital indices
           indicating which orbitals should be considered doubly occupied.
         - active_indices: A list of spatial orbital indices indicating
           which orbitals should be considered active.
         - 1 and 2 body integrals.
    Returns:
        tuple: Tuple with the following entries:
        **core_constant**: Adjustment to constant shift in Hamiltonian
        from integrating out core orbitals
        **one_body_integrals_new**: one-electron integrals over active space.
        **two_body_integrals_new**: two-electron integrals over active space.
    """
    # Determine core Energy from frozen MOs
    core_energy = 0
    for i in frozen_indices:
        # core_energy += 2 * h_[i, i]
        for j in frozen_indices:
            core_energy += U_[i, i, j, j]

    # Modified one-electron integrals
    h_act = h_.copy()
    for t in active_indices:
        for u in active_indices:
            for i in frozen_indices:
                h_act[t, u] += U_[i, i, t, u]

    return (core_energy,
            h_act[np.ix_(active_indices, active_indices)],
            U_[np.ix_(active_indices, active_indices, active_indices, active_indices)])


def calculate_integrals(mol_obj, site_id, p, two_e_interactions, V=None):
    # Storing the cluster orbitals ( will be used as an active space )
    N_MO_cluster = 2 * len(site_id)
    N_elec_env = mol_obj.Ne - N_MO_cluster
    if N_elec_env < 0:
        raise Exception(f"Problem with the number of electrons! Not enough electrons in the environment ({N_elec_env})")
    if N_elec_env > 2 * (p.shape[0] - N_MO_cluster):
        raise Exception(f"Problem with the number of electrons! Too many electrons in the environment ({N_elec_env})")
    C_cluster_active = p[:, 0:N_MO_cluster]

    # Evaluating the environment MOs
    # (A) Doubly occupied MOs in the environment
    C_occ_env = np.zeros((p.shape[0], N_elec_env // 2))
    C_occ = mol_obj.wf_ks.copy()
    C_occ[:, (mol_obj.Ne//2):] = 0
    Sub_block_env = (2 * p.T @ C_occ @ C_occ.T @ p)[N_MO_cluster:,
                    N_MO_cluster:]  # Subblock for the local environement orbitals
    Nat_occ, Nat_orb = np.linalg.eigh(Sub_block_env)  # Evaluating the assocaited natural orbitals
    C_occ_env[N_MO_cluster:, :] = np.fliplr(Nat_orb)[:, 0:N_elec_env // 2]  # Expressed the latter in the Householder basis
    C_occ_env = p @ C_occ_env  # We express the Env. occ. MOs in the local OAO basis

    # # (B) Virtual MOs in the environment
    C_virt_env = np.zeros((p.shape[0], p.shape[1] - N_elec_env // 2 - N_MO_cluster))
    C_virt_env[N_MO_cluster:, :] = np.fliplr(Nat_orb)[:, N_elec_env // 2:]
    C_virt_env = p @ C_virt_env

    # Building the effective MO coefficient matrix for the active space problem
    C_ht = np.block([C_occ_env, C_cluster_active, C_virt_env])

    # two_e_interactions = np.zeros((mol_obj.Ns, mol_obj.Ns, mol_obj.Ns, mol_obj.Ns))
    # two_e_interactions[range(mol_obj.Ns), range(mol_obj.Ns), range(mol_obj.Ns), range(mol_obj.Ns)] = mol_obj.u
    frozen_indices = list(np.arange(C_occ_env.shape[1]))
    active_indices = list(np.arange(C_cluster_active.shape[1]) + C_occ_env.shape[1])
    if mol_obj.ab_initio:
        two_e_int_tilde = mol_obj.transform_g_term(C_ht, site_id, bath_type=BATH_TYPE, return_whole=True)
        h_tilde_cas = C_ht.T @ (change_indices(mol_obj.h, site_id) + np.diag(change_indices(mol_obj.v_s, site_id))) @ C_ht
        ret = qnb.tools.qc_get_active_space_integrals(h_tilde_cas, two_e_int_tilde, frozen_indices, active_indices)
        core_energy, h_tilde_as, u_dimer_as = ret
        v_dimer_as = None
    else:
        two_e_int_tilde = np.einsum('ip, jq, kr, ls, ijkl', C_ht, C_ht, C_ht, C_ht, two_e_interactions)
        h_tilde_cas = C_ht.T @ (change_indices(mol_obj.t, site_id) + np.diag(change_indices(mol_obj.v_s,
                                                                                            site_id))) @ C_ht
        ret = fh_get_active_space_integrals(h_tilde_cas, two_e_int_tilde, frozen_indices, active_indices)
        core_energy, h_tilde_as, u_dimer_as = ret
        if V is None:
            v_dimer_as = None
        else:

            V = mol_obj.transform_v_term(C_ht, site_id, return_whole=True)
            ret = get_active_space_integrals_fh_v_term(V, list(np.arange(C_occ_env.shape[1])),
                                                       list(np.arange(C_cluster_active.shape[1]) + C_occ_env.shape[1]))
            core_energy2, h_tilde_as2, v_dimer_as = ret
            core_energy += core_energy2
            h_tilde_as += h_tilde_as2
    return core_energy, h_tilde_as, u_dimer_as, v_dimer_as


def abs_norm(x):
    return np.max(np.abs(x))


def change_indices(array_inp: np.array, site_id: typing.Union[int, typing.List[int]],
                   to_index: typing.Union[int, typing.List[int], None] = None):
    """
    Function that changes indices of the 1/2/4D arrays. This is often used with the Householder transformation
    where it is important that fragments are the first in the list.
    :param array_inp: 1/2/4D array
    :param site_id: integer or list of indices. it will put sites in first len(site_id) places by default
    :param to_index: If None then it will change indices in a way where site_id will become 0th, 1st, ... indices.
    If there is a list of integers provided to to_index then columns indices will change site_id --> to_index
    :return: matrix with swapped indices. It has same dimensions as array_inp. It returns a copy of the original object.
    """
    array = np.copy(array_inp)
    # region normalization of the site_id and to_index
    if isinstance(site_id, (int, np.int64, np.int32, float, np.float64)):
        site_id = [int(site_id)]
        if to_index is not None:
            to_index = [int(to_index)]
        else:
            to_index = [0]
    else:
        site_id = [int(i) for i in site_id]

        if to_index is not None:
            to_index = [int(i) for i in to_index]
        else:
            to_index = list(range(len(site_id)))
            # It mustn't be a numpy array because we want + operator to be concatenation and not sum of arrays
    # endregion
    if site_id != to_index:  # if they are same then we don't have to do anything
        set_list = to_index + site_id
        get_list = site_id + to_index
        if len(np.unique(set_list)) != len(set_list):  # in this case to_index, site_id overlap which creates problems
            # In this case simple approach don't work since we are writing and reading from same column. We have to get
            # rid of the duplicate entries! we do this by next formula:
            set_list2 = []
            get_list2 = []
            buffer_set = 0
            buffer_get = 0
            set_list = to_index + site_id
            get_list = site_id + to_index
            for i in range(len(set_list)):
                while i + buffer_set < len(set_list) and set_list[i + buffer_set] in set_list2:
                    buffer_set += 1
                while i + buffer_get < len(get_list) and get_list[i + buffer_get] in get_list2:
                    buffer_get += 1
                if i + buffer_set == len(set_list) or i + buffer_get == len(get_list):
                    break
                set_list2.append(set_list[buffer_set + i])
                get_list2.append(get_list[buffer_get + i])
            get_list = get_list2
            set_list = set_list2
        # region Actual transformation of the array
        if array_inp.ndim == 1:
            array[set_list] = array[get_list]
        elif array_inp.ndim == 2:
            array[:, set_list] = array[:, get_list]
            array[set_list, :] = array[get_list, :]
        elif array_inp.ndim == 4:
            array[:, :, :, set_list] = array[:, :, :, get_list]
            array[:, :, set_list, :] = array[:, :, get_list, :]
            array[:, set_list, :, :] = array[:, get_list, :, :]
            array[set_list, :, :, :] = array[get_list, :, :, :]
        else:
            raise Exception("number of dimension is not supported")
        # endregion
    return array


def cangen(t, cannon_list):
    """
    A method to define chemically non-equivalent sites in model Hamiltonian
    :param t: 1-electron matrix
    :param cannon_list: starting "cannon" that sets very different site (with e.g., different external potentials apart.
    :param g: 2-electron matrix --> Has to be implemented!!!!!
    :return: list of equivalent sites
    """

    def normalize_values(starting_vals):
        """
        Helper function for the cangen. It sorts values with arbitrary range to range from 0 to N-1
        :param starting_vals: list of floats
        :return:
        """
        starting_vals2 = np.zeros(len(starting_vals), int) + 1
        arg_sort = np.argsort(starting_vals)
        for k in range(1, len(arg_sort)):
            if not starting_vals[arg_sort[k]] > starting_vals[arg_sort[k - 1]] + 1e-4:
                starting_vals2[k] = starting_vals2[k - 1]
            else:
                starting_vals2[k] = starting_vals2[k - 1] + 1
        for k in range(len(starting_vals)):
            starting_vals[arg_sort[k]] = starting_vals2[k]
        return np.array(starting_vals)

    order = normalize_values(cannon_list)
    order_old = np.zeros(len(order), int) - 100
    iter1 = 0
    while iter1 < 15 or sum(order - order_old) != 0:

        order_new = np.zeros(len(order), int)
        for i in range(len(cannon_list)):
            order_new[i] = order[i] * 100 + np.sum(t[i] * order)
        iter1 += 1
        order_old = order.copy()
        order = normalize_values(order_new)
    ret_list = []
    for value in order:
        new_val = tuple(np.where(order == value)[0])
        if new_val not in ret_list:
            ret_list.append(new_val)
    return ret_list


class Molecule:
    """
    This is a class that is meant for the single impurity calculations
    """

    def __init__(self, site_number, electron_number):
        # Basic data about system
        self.Ns = site_number
        self.Ne = electron_number

        # Parameters for the system
        self.u = np.array((), dtype=np.float64)  # for the start only 1D array and not 4d tensor
        self.t = np.array((), dtype=np.float64)  # 2D one electron Hamiltonian with all terms for i != j
        self.v_ext = np.array((), dtype=np.float64)  # 1D one electron Hamiltonian with all terms for i == j
        self.v_term = None

        # density matrix
        self.y_a = np.array((), dtype=np.float64)  # y --> gamma, a --> alpha so this indicates it is only per one spin

        # KS
        self.v_s = np.zeros(self.Ns, dtype=np.float64)  # Kohn-Sham potential
        self.h_ks = np.array((), dtype=np.float64)
        self.wf_ks = np.array((), dtype=np.float64)  # Kohn-Sham wave function
        self.epsilon_s = np.array((), dtype=np.float64)  # Kohn-Sham energies
        self.n_ks = np.array((), dtype=np.float64)  # Densities of KS system
        self.v_hxc = np.zeros(self.Ns, dtype=np.float64)  # Hartree exchange correlation potential
        self.imp_potential = np.zeros(self.Ns, dtype=np.float64)

        # Energy
        self.kinetic_contributions = np.zeros((), dtype=np.float64)
        # This is where \hat t^{(i)} are going to be written
        self.onsite_repulsion = np.zeros((), dtype=np.float64)
        self.v_term_repulsion = np.zeros((), dtype=np.float64)
        self.energy_contributions = tuple()
        self.per_site_energy = np.array(())

        # Quant_NBody objects for embedding
        self.embedded_mol_dict = dict()
        self.embedded_mol = class_qnb.HamiltonianV2(2, 2)
        self.embedded_mol.build_operator_a_dagger_a(silent=True)

        self.optimize_progress_input = []  # variables that save progression of the calculation
        self.optimize_progress_output = []
        self.density_progress = []  # This object is used for gathering changes in the density over iterations
        self.v_hxc_progress = []

        self.equiv_atom_groups = []
        self.equiv_atom_groups_reverse = []
        self.blocks = []  # This 2d list tells us which sites are merged into which clusters [[c0s0, c0s1,..],[c1s0...]]
        self.equiv_block_groups = []  # This 2d list tells us which blocks are equivalent. [0, 1], [2]] means that first
        # block is same as second and 3rd is different
        self.equiv_atoms_in_block = []  # This 2d list is important so we know which atoms have the same impurity
        # potentials. This list is important because we have to optimize len(..) - 1 Hxc potentials

        self.ab_initio = False
        self.h = np.array([])  # Ready for ab-initio Hamiltonian
        self.g = np.array([])

    def add_parameters(self, u, t, v_ext,
                       v_term_repulsion_ratio: typing.Union[bool, float] = False,
                       blocks: typing.Union[bool, typing.List[typing.List[int]]] = False):
        """
        Method that writes all matrices and calculates equivalent sites
        :param blocks: If False than the single impurity embedding is executed, else it has to be 2d array
        that tells which sites are embedded together.
        :param u: 1D array that represents on-site repulsion
        :param t: 2D array that represents hopping operator integrals (or any other 1-electron operator)
        :param v_ext: 1D array representing external potential vector
        :param v_term_repulsion_ratio: number between 0-1 that sets nearest-neighbor interactions. number is percentage
        how much of the repulsion is present in NNI relative to on-site repulsion
        (0.5 would mean that V_ij = 0.5(0.5U_i + 0.5 U_j)|)
        :return: None
        """
        if len(u) != self.Ns or len(t) != self.Ns or len(v_ext) != self.Ns:  # test for the dimensions
            raise Exception(f"Problem with size of matrices: U={len(u)}, t={len(t)}, v_ext={len(v_ext)}")
        self.u = u
        self.t = t
        self.v_ext = v_ext
        for i in range(self.Ns):
            if v_term_repulsion_ratio:
                self.v_term = np.zeros((self.Ns, self.Ns))
                for a in range(len(self.t)):
                    for b in range(len(self.t)):
                        if self.t[a, b] != 0 and a > b:
                            self.v_term[a, b] = v_term_repulsion_ratio * 0.5 * (u[a] + u[b])
                            # self.v_term[b, a] = v_term_repulsion_ratio * 0.5 * (u[a] + u[b])
        if not np.allclose(self.t, self.t.T):
            raise Exception("t matrix should have been symmetric")
        equiv_atom_group_list = cangen(t, np.array(u) * 100 + np.array(v_ext))
        general_logger.info(f'Equivalent atoms: {equiv_atom_group_list}')
        self.equiv_atom_groups = []
        for index, item in enumerate(equiv_atom_group_list):
            self.equiv_atom_groups.append(tuple(item))
        self.equiv_atom_groups_reverse = np.zeros(self.Ns, int)
        for group_id, atom_list in enumerate(self.equiv_atom_groups):
            for site_id in atom_list:
                self.equiv_atom_groups_reverse[site_id] = group_id
        self.prepare_for_block(blocks)

    def prepare_for_block(self, blocks: typing.Union[bool, typing.List[typing.List[int]]] = False):
        """
        This method takes in the block list and generates all necessary objects to make block Householder working
        :param blocks: 2d list looking like [[sites in cluster 0, ..], [sites in cluster 1, ...], ...]
        :return: None
        """
        if blocks is False:
            blocks = [[i] for i in range(self.Ns)]
        n_blocks = len(blocks)
        if 0 not in blocks[0]:
            general_logger.error('site 0 must be in the first block')
            return 0
        self.blocks = blocks
        normalized_blocks = [[int(self.equiv_atom_groups_reverse[j]) for j in i] for i in blocks]
        # normalized_blocks is an array of groups in each block each site from block is replaced by its equiv group
        ignored = []
        equiv_block_list = []
        for i in range(n_blocks):  # Find blocks that are equivalent
            if i in ignored:
                continue
            equiv_block_list.append([i])
            for j in range(i + 1, n_blocks):
                if i in ignored:
                    continue
                if sorted(normalized_blocks[i]) == sorted(normalized_blocks[j]):
                    equiv_block_list[-1].append(j)
                    ignored.append(j)
        self.equiv_block_groups = equiv_block_list
        ignore = []
        equiv_atoms_in_block = []
        for i in range(self.Ns):  # Identifying equivalent sites in block
            if i in ignore:
                continue
            atom_group = int(self.equiv_atom_groups_reverse[i])
            equivalent_atoms = self.equiv_atom_groups[atom_group]
            block_i = [i in h for h in self.blocks].index(True)
            block_group_i = [block_i in h for h in self.equiv_block_groups].index(True)
            equiv_atoms_in_block.append([i])
            for j in equivalent_atoms:
                if i == j or j in ignore:
                    continue
                    # Only if i!=j and if i>j so we don't count things twice
                block_j = [j in h for h in self.blocks].index(True)
                block_group_j = [block_j in h for h in self.equiv_block_groups].index(True)
                if block_group_j == block_group_i:
                    equiv_atoms_in_block[-1].append(j)
                    ignore.append(j)
            ignore.append(i)
        general_logger.info(f"Equivalent blocks: {self.equiv_block_groups}")
        general_logger.info(f"Equivalent atoms inside blocks: {equiv_atoms_in_block}")
        self.equiv_atoms_in_block = equiv_atoms_in_block

        for block_i in blocks:  # now each cluster can have different size so we need to create objects for each size
            block_size = len(block_i)
            self.embedded_mol_dict[block_size] = class_qnb.HamiltonianV2(block_size * 2, block_size * 2)
            self.embedded_mol_dict[block_size].build_operator_a_dagger_a(silent=True)

        self.kinetic_contributions = np.zeros(len(self.blocks), dtype=np.float64)
        self.onsite_repulsion = np.zeros(len(self.blocks), dtype=np.float64)
        self.v_term_repulsion = np.zeros(len(self.blocks), dtype=np.float64)

    def calculate_ks(self, prevent_extreme_values=False):
        """
        Method that generates KS Hamiltonian and calculates wave function, 1RDM.
        It can be only called after add_parameters method.
        :param prevent_extreme_values: If True then the method will return error if some orbitals are too close to
        occupation numbers 0 or 2 (in this case LPFET algorithm can't find extreme impurity potential.
        :return: None
        """
        self.v_s = self.v_hxc + self.v_ext
        if self.ab_initio:
            self.h_ks = self.h + np.diag(self.v_s)
        else:
            self.h_ks = self.t + np.diag(self.v_s)
        try:
            self.epsilon_s, self.wf_ks = np.linalg.eigh(self.h_ks, 'U')
        except np.linalg.LinAlgError as e:
            raise Exception(f"calculate_ks did not converge\n{essentials.print_matrix(self.h_ks)}") from e
        self.y_a = essentials.generate_1rdm(self.Ns, self.Ne, self.wf_ks)
        if self.Ne > 0:
            e_homo = self.epsilon_s[(self.Ne - 1) // 2]
        else:
            e_homo = self.epsilon_s[0]
        e_lumo = self.epsilon_s[self.Ne // 2]
        if np.isclose(e_lumo, e_homo):  # Check to make sure that there is no degeneracy
            raise errors.DegeneratedStatesError(self.Ne, self.epsilon_s)
        self.n_ks = np.copy(self.y_a.diagonal())

        if prevent_extreme_values:
            min_distance_from_extremes = 1e-5
            # In LPFET we don't want to have either fully occupied impurities or fully empty site because then non of
            # the chemical impurity potential will be able to match density from KS-DFT. for some random dimer and
            # mu_imp == 50 we get occupation 1.99984983
            if np.any(np.isclose(self.n_ks, 0, atol=min_distance_from_extremes)):
                raise errors.EmptyFullOrbitalError(self.n_ks, self.n_ks[np.isclose(self.n_ks, 0, atol=1e-3)])

            if np.any(np.isclose(self.n_ks, 2, atol=min_distance_from_extremes)):
                raise errors.EmptyFullOrbitalError(self.n_ks, self.n_ks[np.isclose(self.n_ks, 2, atol=1e-3)])

    def calculate_energy(self, silent=False):
        """
        Method that can be called after the find_solution_as_root that already calculates all contributions
        :param silent: If False then nice table of different contributions is printed.
        :return: total electronic energy of the system
        """
        per_site_array = np.zeros(len(self.blocks), dtype=[('tot', float), ('kin', float), ('v_ext', float), ('u', float),
                                                  ('v_term', float)])
        per_site_array['kin'] = self.kinetic_contributions  # call from precalculated values
        group_density = np.zeros(len(self.blocks))
        for group_id, group in enumerate(self.blocks):
            per_site_array['v_ext'][group_id] = np.sum(self.v_ext[group] * self.n_ks[group])
            group_density[group_id] = np.sum(self.n_ks[group])
        per_site_array['u'] = self.onsite_repulsion
        per_site_array['v_term'] = self.v_term_repulsion
        per_site_array['tot'] = np.sum(np.array(per_site_array[['kin', 'v_ext', 'u', 'v_term']].tolist()), axis=1)
        kinetic_contribution = np.sum(per_site_array['kin'])
        v_ext_contribution = np.sum(per_site_array['v_ext'])
        u_contribution = np.sum(self.onsite_repulsion)
        v_term_contribution = np.sum(self.v_term_repulsion)
        total_energy = kinetic_contribution + v_ext_contribution + u_contribution + v_term_contribution
        self.energy_contributions = (total_energy, kinetic_contribution, v_ext_contribution,
                                     u_contribution, v_term_contribution)
        self.per_site_energy = per_site_array
        if not silent:  # print into logger nice table
            general_logger.info(
                f'\n{"site":30s}{" ".join([f"{str(i)[1:-1]:>9s}" for i in self.blocks])}{"total":>12s}'
                f'\n{"Kinetic energy":30s}'
                f'{" ".join([f"{i:9.4f}" for i in self.kinetic_contributions])}{kinetic_contribution:12.7f}\n'
                f'{"External potential energy":30s}{" ".join([f"{i:9.4f}" for i in per_site_array["v_ext"]])}'
                f'{v_ext_contribution:12.7f}\n{"On-site repulsion":30s}'
                f'{" ".join([f"{i:9.4f}" for i in self.onsite_repulsion])}{u_contribution:12.7f}\n'
                f'{"V-term repulsion":30s}{" ".join([f"{i:9.4f}" for i in self.v_term_repulsion])}'
                f'{v_term_contribution:12.7f}\n'
                f'{"Occupations":30s}{" ".join([f"{i:9.4f}" for i in group_density])}{np.sum(self.n_ks) * 2:12.7f}')
            general_logger.info(f'{"_" * 20}\nTotal energy:{total_energy}')
        return total_energy

    def compare_densities_fci(
            self, pass_object: Union[bool, class_qnb.HamiltonianV2] = False,
            calculate_per_site=False) -> typing.Tuple[np.array, class_qnb.HamiltonianV2, tuple, np.array]:
        """
        Calculate reference calculation with the exact diagonalization of the whole system with the help of quantnbody.
        It is only working with Hubbard models!
        :param pass_object: If False then it creates new HamiltonianV2 object for the whole molecule. Otherwise it can
        be passed to speed up the calculation
        :param calculate_per_site: If True then also per site energies get returned. The drawback is that 2RDM needs to
        be calculated which can be slow
        :return: tuple of: (1RDM, HamiltonianV2, (total_energy, kinetic_contribution, v_ext_contribution,
                                    u_contribution, v_term_contribution), per_site_array)
        """
        if type(pass_object) != bool:
            mol_full = pass_object
        else:
            mol_full = class_qnb.HamiltonianV2(self.Ns, self.Ne)
            mol_full.build_operator_a_dagger_a()
        u4d = np.zeros((self.Ns, self.Ns, self.Ns, self.Ns))
        for i in range(self.Ns):
            u4d[i, i, i, i] = self.u[i]
        v_tilde = None
        if self.v_term is not None:
            p = np.eye(self.Ns)
            v_tilde = np.einsum('ip, iq, jr, js, ij -> pqrs', p, p, p, p, self.v_term)
        mol_full.build_hamiltonian_fermi_hubbard(self.t + np.diag(self.v_ext), u4d, v_term=v_tilde)
        mol_full.diagonalize_hamiltonian()
        y_ab = mol_full.calculate_1rdm_spin_free()
        densities = y_ab.diagonal()
        kinetic_contribution = np.sum(y_ab * self.t)
        v_ext_contribution = np.sum(self.v_ext * densities)
        total_energy = mol_full.eig_values[0]
        u_contribution = total_energy - kinetic_contribution - v_ext_contribution

        if calculate_per_site:
            per_site_array = np.zeros(self.Ns, dtype=[('tot', float), ('kin', float), ('v_ext', float), ('u', float),
                                                      ('v_term', float)])

            two_rdm_u = mol_full.build_2rdm_fh_on_site_repulsion(u4d)
            if self.v_term is not None:
                two_rdm_v_term = mol_full.build_2rdm_fh_dipolar_interactions(v_tilde)
                on_site_repulsion_array = two_rdm_v_term * v_tilde
                for site in range(self.Ns):
                    per_site_array[site]['v_term'] = 0.5 * (np.sum(on_site_repulsion_array[site, site, :, :]) +
                                                            np.sum(on_site_repulsion_array[:, :, site, site]))
            t_multiplied_matrix = y_ab * self.t
            for site in range(self.Ns):
                per_site_array[site]['v_ext'] = self.v_ext[site] * densities[site]
                per_site_array[site]['u'] = two_rdm_u[site, site, site, site] * self.u[site]
                per_site_array[site]['kin'] = np.sum(t_multiplied_matrix[site])
                per_site_array[site]['tot'] = sum(per_site_array[site])
            u_contribution = np.sum(per_site_array['u'])
            v_term_contribution = np.sum(per_site_array['v_term'])

            return y_ab, mol_full, (total_energy, kinetic_contribution, v_ext_contribution,
                                    u_contribution, v_term_contribution), per_site_array
        general_logger.info(f"FCI densities (per spin):{densities}")
        general_logger.info(f'FCI energy: {mol_full.eig_values[0]}')
        return y_ab, mol_full, (total_energy, kinetic_contribution, v_ext_contribution,
                                u_contribution, 0), np.array([])

    def clear_object(self):
        """
        This method is used to clear all data that would mess with the self consistent loop without redoing calculation
        of the a_dagger_a. This enables us faster calculations
        :return: None
        """
        self.u = np.array((), dtype=np.float64)
        self.t = np.array((), dtype=np.float64)
        self.v_ext = np.array((), dtype=np.float64)
        self.equiv_atom_groups = []
        self.v_hxc = np.zeros(self.Ns, dtype=np.float64)  # Hartree exchange correlation potential
        self.density_progress = []  # This object is used for gathering changes in the density over iterations
        self.v_hxc_progress = []

        self.optimize_progress_input = []
        self.optimize_progress_output = []

    @staticmethod
    def cost_function_2(mu_imp, embedded_mol, h_tilde_dimer, two_e_int, desired_density, v_tilde, ab_initio=False):
        """
        Function that calculates the correlated cluster wave function and compares it with the desired density (the KS
         density).
        :param mu_imp: impurity potential. It is either a float or 1D array in case of block Householder transformation.
        :param embedded_mol: HamiltonianV2 object: it is an Hamiltonian object with already calculated a_dagger_a.
        :param h_tilde_dimer: 1-electron operator integrals (without impurity potential)
        :param two_e_int: 2-electron operator integrals. If ab-initio the this is 4D array of g integrals and in case of
        Hubbard model it is the 4D array of on-site repulsion U_ijkl
        :param desired_density: Fragment densities that are subtracted from the resulting density of correlated wave
        function. In this way this can be used as a cost function.
        :param v_tilde: if None then it is 0, also not used if ab_initio is True. Otherwise 4D array of integrals that
        are in the correct space
        :param ab_initio: If True then it is building quantum chemistry hamiltonian. Otherwise it is Hubbard molecule
        :return: differences between occupations in correlated cluster wave function and input wave function.
        Depending on the input returning value ban be a float or 1D np.array.
        """
        # mu_imp = mu_imp[0]
        global ITERATION_NUM, ROOT_FINDING_LIST_INPUT, ROOT_FINDING_LIST_OUTPUT
        cluster_size = h_tilde_dimer.shape[0]
        half_diagonal = np.arange(cluster_size / 2, dtype=int)
        ITERATION_NUM += 1
        mu_imp_array = np.zeros((cluster_size, cluster_size))
        mu_imp_array[half_diagonal, half_diagonal] = mu_imp
        if ab_initio:
            embedded_mol.build_hamiltonian_quantum_chemistry(h_tilde_dimer - mu_imp_array, two_e_int)
        else:
            embedded_mol.build_hamiltonian_fermi_hubbard(h_tilde_dimer - mu_imp_array, two_e_int, v_term=v_tilde)
        embedded_mol.diagonalize_hamiltonian()
        density_dimer = embedded_mol.calculate_1rdm_spin_free(index=0)
        result = density_dimer[half_diagonal, half_diagonal] - desired_density
        if hasattr(mu_imp, 'len'):
            ROOT_FINDING_LIST_INPUT.append(mu_imp.copy())
            ROOT_FINDING_LIST_OUTPUT.append(result.copy())
        else:
            ROOT_FINDING_LIST_INPUT.append([mu_imp])
            if hasattr(result, '__setattr__'):
                ROOT_FINDING_LIST_OUTPUT.append(result)
            else:
                ROOT_FINDING_LIST_OUTPUT.append([result])

        general_logger.log(5,
                           f"|||| cost function 2: Input: {mu_imp}; Output: {result}; "
                           f"desired density: {desired_density}; ab-initio: {ab_initio}")
        return result

    def find_solution_as_root(self, starting_approximation=None):
        """
        This is a function that actually finds the solution. Main part is the optimizing algorithm.
        :param starting_approximation: None for default value starting with some generic approximation. otherwise a
        vector with length of chemically nonequivalent sites. First value represents impurity potential on site 0 and
        other represent v_Hxc adjusted to have v_Hxc[0]=0. This is an input for the optimization algorithm.
        :return: same vector as described above with optimized values
        """
        # region Starting approximation for v_Hxc and mu_imp_0
        if starting_approximation is None:  # Get some starting approximation for the optimization algorithm
            starting_approximation = np.zeros(len(self.equiv_atoms_in_block), float)
            for group_key, group_tuple in enumerate(self.equiv_atoms_in_block):
                group_element_site = group_tuple[0]
                starting_approximation[group_key] = (self.v_ext[0] - self.v_ext[group_element_site]) * 0.5
        elif len(starting_approximation) != len(self.equiv_atoms_in_block):
            raise Exception(f'Wrong length of the starting approximation: len(starting_approximation) != '
                            f'len(eq_atom) ({len(starting_approximation)} != {len(self.equiv_atoms_in_block)}')
        general_logger.log(25, f'start of optimization of Hxc potentials, '
                               f'starting approximation = {starting_approximation}')
        # endregion
        self.optimize_progress_output = []
        self.optimize_progress_input = []
        # Optimization algorithm
        model = scipy.optimize.root(self.cost_function_whole,starting_approximation, args=(self,),
                                    options={'maxfev': 200})
        # region Additional tries if the original method fails
        if not model.success:
            general_logger.warning(f'Model was not successful! message:\n{model}\nTrying with another solver')
            model = scipy.optimize.minimize(self.cost_function_whole_minimize, model.x, args=(self,))  # minimization
        optimize_progress_output = np.array(self.optimize_progress_output)
        optimize_progress_input = np.array(self.optimize_progress_input)
        if not model.success or np.sqrt(np.sum(np.square(model.fun))) > 0.001:
            weights = np.sqrt(np.sum(np.square(1 / (optimize_progress_output + 1e-5)), axis=1))
            mask = weights > np.percentile(weights, 95)
            final_approximation1 = np.average(optimize_progress_input[mask], axis=0, weights=weights[mask])
            error1 = self.cost_function_whole(final_approximation1, self)
            final_approximation2 = optimize_progress_input[weights.argsort()[::-1]][0]
            error2 = self.cost_function_whole(final_approximation2, self)
            if abs_norm(error2) > abs_norm(error1):
                final_approximation = final_approximation1
                final_error = error1
            else:
                final_approximation = final_approximation2
                final_error = error2
            if abs_norm(final_error) < 5e-2:
                return final_approximation
            else:
                general_logger.error("Optimization function for the whole system didn't find solution")
                return False
        # endregion
        v_hxc = model.x
        general_logger.log(25, f"Optimized: nfev = {model.nfev}, fun = {model.fun}, x = {model.x}")
        general_logger.info(model)
        global ITER_NUM_ROOT, FUN_ERROR_END
        ITER_NUM_ROOT = model.nfev
        FUN_ERROR_END = model.fun
        return v_hxc

    def transform_v_term(self, p, site_id, calculate_one_site=None, return_whole=False):
        """
        This method takes only interactions corresponding to an impurity and transforms 2D self.v_term into 4D array
        representing interactions in Householder representation.
        :param return_whole: If True than the whole Ns * Ns * Ns * Ns tensor is returned and not only truncated one
        :param p: Householder matrix corresponding to site_id
        :param site_id: array or integer of the site.
        :param calculate_one_site: This is used for calculating per-site energy where we need only one one site
        contributions to be included in the final matrix.
        :return: 4D matrix of transformed NNI term.
        """
        if isinstance(site_id, (int, np.int32, np.int64)):
            site_id = [site_id]
        impurity_size = len(site_id)
        if self.v_term is not None:  # If it is None then there is no NNI.
            v_term = np.zeros((self.Ns, self.Ns))
            if calculate_one_site is None:
                change_id_obj = site_id
            else:
                change_id_obj = calculate_one_site
            v_term[change_id_obj, :] += self.v_term[change_id_obj, :] / 2
            v_term[:, change_id_obj] += self.v_term[:, change_id_obj] / 2
            if site_id == list(range(impurity_size)):
                v_term_correct_indices = v_term
            else:
                v_term_correct_indices = change_indices(v_term, site_id)
            if return_whole:
                p_trunc = p
            else:
                p_trunc = p[:, :2 * impurity_size]
            v_tilde = np.einsum('ip, iq, jr, js, ij -> pqrs', p_trunc, p_trunc, p_trunc, p_trunc,
                                v_term_correct_indices)
        else:
            v_tilde = None
        return v_tilde

    @staticmethod
    def cost_function_whole(v_hxc_approximation: np.array, mol_obj: 'Molecule') -> np.array:
        """
        Block version of the cost function.
        :param v_hxc_approximation: Vector with length of number of non-equivalent sites. First value represents
        mu_imp_0 and next values represent v_Hxc_i assuming that v_Hxc_0 = 0.
        :param mol_obj: it is Molecule object with all matrices loaded. If it was normal method it would be self but
        because this function is meant for the optimizer, the first argument has to be X.
        :return: Error vector or <Psi^i|n_i|Psi^i> - <Phi|n_i|Phi>
        """
        global ROOT_FINDING_LIST_INPUT, ROOT_FINDING_LIST_OUTPUT
        mol_obj.optimize_progress_input.append(v_hxc_approximation.copy())
        mol_obj.v_hxc = np.zeros(mol_obj.Ns, float)
        for group_id, group_site_tuple in enumerate(mol_obj.equiv_atoms_in_block):
            for site_id_i in group_site_tuple:  # setting Hxc potentials for the iteration
                if group_id != 0:
                    mol_obj.v_hxc[site_id_i] = v_hxc_approximation[group_id]
                else:
                    mol_obj.v_hxc[site_id_i] = 0
        str_starting_v_hxc = ''.join([f'{cell:+10.3f}' for cell in mol_obj.v_hxc[1:]])
        general_logger.log(15, f"| Start of cost function 1: μ={v_hxc_approximation[0]:+10.3f},"
                               f" v_hxc = {str_starting_v_hxc}")
        mol_obj.v_hxc_progress.append(mol_obj.v_hxc)
        mol_obj.calculate_ks(prevent_extreme_values=False)
        mol_obj.density_progress.append(mol_obj.n_ks)
        output_array_non_reduced = np.zeros(mol_obj.Ns, float) * np.nan
        output_array = np.zeros(len(mol_obj.equiv_atoms_in_block), float) * np.nan
        mu_imp_first = v_hxc_approximation[0]

        for site_group, group_tuple in enumerate(mol_obj.equiv_block_groups):
            block_id = group_tuple[0]
            site_id = mol_obj.blocks[block_id]
            block_size = len(site_id)

            general_logger.log(10, f"|| Site id: {site_id}")
            y_a_correct_imp = change_indices(mol_obj.y_a, site_id)
            p, hh_rest = householder_transformation(y_a_correct_imp, block_size)
            if mol_obj.ab_initio:
                h_tilde = p @ (change_indices(mol_obj.h, site_id) + np.diag(change_indices(mol_obj.v_s, site_id))) @ p
                two_electron_interactions = mol_obj.transform_g_term(p, site_id)
                v_tilde = None
                t_correct_indices = None
            else:
                t_correct_indices = change_indices(mol_obj.t, site_id)
                h_tilde = p @ (t_correct_indices + np.diag(change_indices(mol_obj.v_s, site_id))) @ p
                v_tilde = mol_obj.transform_v_term(p, site_id, return_whole=ACTIVE_SPACE_CALCULATION)
                if ACTIVE_SPACE_CALCULATION:
                    u_0_dimer = np.zeros((mol_obj.Ns, mol_obj.Ns, mol_obj.Ns, mol_obj.Ns), dtype=np.float64)
                else:
                    u_0_dimer = np.zeros((block_size * 2, block_size * 2, block_size * 2, block_size * 2),
                                         dtype=np.float64)
                range1 = np.arange(len(site_id))
                u_0_dimer[range1, range1, range1, range1] += mol_obj.u[site_id]
                two_electron_interactions = u_0_dimer
            h_tilde_dimer = h_tilde[:block_size * 2, :block_size * 2]

            mu_imp = mu_imp_first + mol_obj.v_hxc[site_id]
            # h_tilde_dimer_old = h_tilde_dimer.copy()
            # two_electron_interactions_old = two_electron_interactions.copy()
            # v_tilde_old = v_tilde.copy()
            if ACTIVE_SPACE_CALCULATION:
                ret = calculate_integrals(mol_obj, site_id, p, two_electron_interactions, V=True)
                core_energy, h_tilde_dimer_as, two_electron_interactions, v_tilde = ret
                h_tilde_correction = h_tilde_dimer_as - h_tilde_dimer
                h_tilde_dimer = h_tilde_dimer_as
                if core_energy != 0:
                    print("CORE ENERGY: ", core_energy)
            else:
                h_tilde_correction = 0
            error_i = mol_obj.cost_function_2(mu_imp, mol_obj.embedded_mol_dict[block_size], h_tilde_dimer,
                                              two_electron_interactions, mol_obj.n_ks[site_id], v_tilde,
                                              mol_obj.ab_initio)
            general_logger.log(10, f"||| Higher accuracy method gave error = {error_i} for mu_imp={mu_imp}")
            output_array_non_reduced[site_id] = error_i

            for index1, one_site_id in enumerate(site_id):
                output_array[[one_site_id in i for i in mol_obj.equiv_atoms_in_block].index(True)] = error_i[index1]
                # Point is to get on which index of equiv_atoms_in_block is each site and write error_i to it

            # energy contributions
            if mol_obj.ab_initio:
                mol_obj.update_cluster_energy_qc(group_tuple, p, h_tilde_correction, two_electron_interactions)
            else:
                mol_obj.update_cluster_energy_fh(group_tuple, p, h_tilde_correction, two_electron_interactions, v_tilde,
                                                 t_correct_indices)

        max_dev = np.max(np.abs(output_array))
        general_logger.log(
            20, f"| End of cost function 1: for input "
                f"{''.join(['{num:{dec}}'.format(num=cell, dec='+10.3f') for cell in mol_obj.v_hxc])}"
                f" error is {''.join(['{num:{dec}}'.format(num=cell, dec='+10.2e') for cell in output_array])} "
                f" (max deviation = {max_dev})")
        mol_obj.optimize_progress_output.append(output_array.copy())
        return output_array

    @staticmethod
    def cost_function_whole_minimize(v_hxc_approximation: np.array, mol_obj: 'Molecule'):
        ret_vec = mol_obj.cost_function_whole(v_hxc_approximation, mol_obj)
        ret_vec = np.sum(np.square(ret_vec))
        return ret_vec


    def update_cluster_energy_fh(self, group_tuple, p, h_tilde_correction, two_electron_interactions, v_tilde,
                                                 t_correct_indices):
        """
        Based on the correlated wave function results this method calculates energy contributions for Hubbard molecule
        that correspond to this cluster.
        :return: None
        """
        block_id = group_tuple[0]
        site_id = self.blocks[block_id]
        block_size = len(site_id)
        one_rdm_c = self.embedded_mol_dict[block_size].one_rdm
        two_rdm_c = self.embedded_mol_dict[block_size].build_2rdm_fh_on_site_repulsion(two_electron_interactions)

        if self.v_term is not None:
            two_rdm_v_term = self.embedded_mol_dict[block_size].build_2rdm_fh_dipolar_interactions(v_tilde)
            v_term_repulsion_i = np.sum(two_rdm_v_term * v_tilde)
            v_term_repulsion_i += np.sum(h_tilde_correction * one_rdm_c)
        else:
            v_term_repulsion_i = 0
        self.v_term_repulsion[group_tuple] = v_term_repulsion_i

        t_tilde_i = t_correct_indices.copy()
        t_tilde_i[[i for i in range(self.Ns) if i not in range(block_size)]] = 0
        t_tilde_i = (p @ t_tilde_i @ p)[:block_size * 2, :block_size * 2]

        self.kinetic_contributions[group_tuple] = np.sum(t_tilde_i * one_rdm_c)
        self.onsite_repulsion[group_tuple] = np.sum(two_rdm_c * two_electron_interactions)

    def update_cluster_energy_qc(self, group_tuple, p, h_tilde_correction, two_electron_interactions):
        """
        Based on the correlated wave function results this method calculates energy contributions for ab-initio
        Hamiltonian that correspond to this cluster.
        :return: None
        """
        block_id = group_tuple[0]
        site_id = self.blocks[block_id]
        block_size = len(site_id)
        one_rdm = self.embedded_mol_dict[block_size].one_rdm
        two_rdm = self.embedded_mol_dict[block_size].build_2rdm_spin_free()
        h_tilde = change_indices(self.h, site_id)
        h_tilde = h_tilde.copy()
        h_tilde[[i for i in range(self.Ns) if i not in range(block_size)]] = 0
        h_tilde = (p @ h_tilde @ p)[:block_size * 2, :block_size * 2]

        self.kinetic_contributions[group_tuple] = np.sum(h_tilde * one_rdm)
        self.onsite_repulsion[group_tuple] = np.sum(two_electron_interactions * two_rdm) / 2
        self.v_term_repulsion[group_tuple] = np.sum(h_tilde_correction * one_rdm)

    def transform_g_term(self, p, site_id, only_one_site=None, bath_type='NIB', return_whole=False):
        """
        Method that takes 2-electron integrals in site representation and it creates integrals in Householder
        representation (taking only part that is related to the sites in the fragment).
        :param bath_type: NIB for non-interacting bath or IB for interacting bath
        :param return_whole: True if non-truncated g integral matrix is required
        :param p: Householder transformation matrix
        :param site_id: list of fragments
        :param only_one_site: when calculating contributions for the per-site energies this is used to take
        contributions of only one site and not the whole cluster.
        :return: 4D array of new 2-electron integrals in Householder representation (copy of original array)
        """
        impurity_size = len(site_id)
        if only_one_site is None:
            change_id = site_id
        else:
            change_id = only_one_site
        if only_one_site is None and bath_type != 'NIB':
            g_term = self.g.copy()
            print("IB")
        else:
            print("NIB")
            g_term = np.zeros((self.Ns, self.Ns, self.Ns, self.Ns))
            g_term[change_id, :, :, :] += self.g[change_id, :, :, :] / 4
            g_term[:, change_id, :, :] += self.g[:, change_id, :, :] / 4
            g_term[:, :, change_id, :] += self.g[:, :, change_id, :] / 4
            g_term[:, :, :, change_id] += self.g[:, :, :, change_id] / 4

        g_term = change_indices(g_term, site_id)
        if return_whole:
            p_trunc = p
        else:
            p_trunc = p[:, :int(2 * impurity_size)]
        g_term = np.einsum('ip, jq, kr, ls, ijkl -> pqrs', p_trunc, p_trunc, p_trunc, p_trunc, g_term)
        return g_term



def find_equivalent_block(mol_obj: Molecule, one_site_id: int):
    """
    Even equivalent atoms can get different approximation for v_Hxc if they are places in cluster in a specific way.
    This function finds which sites are equivalent to one_site_id even after they are split in different blocks.
    :param mol_obj: Molecule object
    :param one_site_id: the site that we want to find equivalent sites to.
    :return: list of equivalent sites to one_site_id
    """
    eq_block = None
    for t1 in range(len(mol_obj.equiv_atoms_in_block)):
        if one_site_id in mol_obj.equiv_atoms_in_block[t1]:
            eq_block = mol_obj.equiv_atoms_in_block[t1]
            break
    if eq_block is None:
        raise Exception(f"Site not found in the equiv_atoms_in_block ({one_site_id} in {mol_obj.equiv_atoms_in_block})")
    return eq_block


def generate_from_graph(sites, connections):
    """
    We can provide graph information and program generates hamiltonian automatically
    :param sites: in the type of: {0:{'v':0, 'U':4}, 1:{'v':1, 'U':4}, 2:{'v':0, 'U':4}, 3:{'v':1, 'U':4}}
    :param connections: {(0, 1):1, (1, 2):1, (2, 3):1, (0,3):1} --> positive t values!!
    :return: h and U parameters
    """
    n_sites = len(sites)
    t = np.zeros((n_sites, n_sites), dtype=np.float64)
    v = np.zeros(n_sites, dtype=np.float64)
    u = np.zeros(n_sites, dtype=np.float64)
    for id1, params in sites.items():
        if 'U' in params:
            u[id1] = params['U']
        elif 'u' in params:
            u[id1] = params['u']
        else:
            raise "Problem with params: " + params
        v[id1] = params['v']
    for pair, param in connections.items():
        t[pair[0], pair[1]] = -param
        t[pair[1], pair[0]] = -param
    return t, v, u


class MoleculeChemistry(Molecule):
    def __init__(self, site_number, electron_number):
        super().__init__(site_number, electron_number)
        self.ab_initio = True

    def add_parameters(self, g, h, equivalent_atoms=None, temp2=False, blocks=False):
        """
        Instead of saving to v_ext, t, u, this overloaded method saves to g, h. ALso since the equivalence algorithm
        doesn't work well blocks array can be supplied manually.
        Basis of integrals has to be ORTHOGONAL!
        :param g: 2-electron integrals in chemist notation g[i,j,k,l] = (ij|kl)
        :param h: 1-electron integrals
        :param equivalent_atoms: optional argument. If provided it has to be in form of Molecule.blocks list
        :param temp2: Not used. Just in the input for the sake of matching number of arguments with overrided function
        :return:
        """
        if len(g) != self.Ns or len(h) != self.Ns:
            raise Exception(f"Problem with size of matrices: U={len(u)}, t={len(t)}, v_ext={len(v_ext)}")
        self.h = h
        self.g = g
        if not np.allclose(self.h, self.h.T):
            raise Exception("t matrix should have been symmetric")
        if equivalent_atoms is None:
            equiv_atom_group_list = cangen(self.h, self.h.diagonal() * 20 + np.round(np.sum(self.g, axis=(1, 2, 3)), 3))
        else:
            equiv_atom_group_list = equivalent_atoms
        general_logger.info(f'Equivalent atoms: {equiv_atom_group_list}')
        self.equiv_atom_groups = []
        for index, item in enumerate(equiv_atom_group_list):
            self.equiv_atom_groups.append(tuple(item))
        self.equiv_atom_groups_reverse = np.zeros(self.Ns, int)
        for group_id, atom_list in enumerate(self.equiv_atom_groups):
            for site_id in atom_list:
                self.equiv_atom_groups_reverse[site_id] = group_id

        self.prepare_for_block(blocks)


if __name__ == "__main__":
    pass
