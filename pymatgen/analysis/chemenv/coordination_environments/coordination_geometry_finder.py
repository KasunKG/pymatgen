# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, unicode_literals

"""
This module contains the main object used to identify the coordination environments in a given structure.
"""

__author__ = "David Waroquiers"
__copyright__ = "Copyright 2012, The Materials Project"
__credits__ = "Geoffroy Hautier"
__version__ = "2.0"
__maintainer__ = "David Waroquiers"
__email__ = "david.waroquiers@gmail.com"
__date__ = "Feb 20, 2016"


import itertools
import logging
import time

from numpy.linalg import svd
from numpy.linalg import norm
from numpy import transpose
from pymatgen.core.structure import Structure
from pymatgen.core.lattice import Lattice
from pymatgen.core.periodic_table import Specie
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.analysis.bond_valence import BVAnalyzer
import numpy as np

from random import shuffle

from pymatgen.analysis.chemenv.utils.coordination_geometry_utils import vectorsToMatrix, rotateCoords, Plane
from pymatgen.analysis.chemenv.utils.coordination_geometry_utils import matrixMultiplication
from pymatgen.analysis.chemenv.utils.coordination_geometry_utils import collinear, separation_in_list
from pymatgen.analysis.chemenv.utils.coordination_geometry_utils import sort_separation
from pymatgen.analysis.chemenv.coordination_environments.coordination_geometries import AllCoordinationGeometries
from pymatgen.analysis.chemenv.coordination_environments.coordination_geometries import EXPLICIT_PERMUTATIONS
from pymatgen.analysis.chemenv.coordination_environments.coordination_geometries import SEPARATION_PLANE
from pymatgen.analysis.chemenv.coordination_environments.structure_environments import ChemicalEnvironments
from pymatgen.analysis.chemenv.coordination_environments.structure_environments import StructureEnvironments
from pymatgen.analysis.chemenv.coordination_environments.voronoi import DetailedVoronoiContainer

debug = False
DIST_TOLERANCES = [0.02, 0.05, 0.1, 0.2, 0.3]


class AbstractGeometry(object):
    """
    Class used to describe a geometry (perfect or distorted)
    """
    def __init__(self, central_site=None, bare_coords=None, centering_type='standard',
                 include_central_site_in_centroid=False):
        """
        Constructor for the abstract geometry
        :param central_site: Coordinates of the central site
        :param bare_coords: Coordinates of the neighbors of the central site
        :param centering_type: How to center the abstract geometry
        :param include_central_site_in_centroid: When the centering is on the centroid, the central site is included
         if this parameter is set to True.
        :raise: ValueError if the parameters are not consistent
        """
        self.bare_centre = np.array(central_site)
        self.bare_points_without_centre = [np.array(bc) for bc in bare_coords]
        self.bare_points_with_centre = [np.array(central_site)]
        self.bare_points_with_centre.extend([np.array(bc) for bc in bare_coords])

        self.centroid_without_centre = np.mean(self.bare_points_without_centre, axis=0)
        self.centroid_with_centre = np.mean(self.bare_points_with_centre, axis=0)

        self._points_wcs_csc = [pp - self.bare_centre for pp in self.bare_points_with_centre]
        self._points_wocs_csc = [pp - self.bare_centre for pp in self.bare_points_without_centre]
        self._points_wcs_ctwcc = [pp - self.centroid_with_centre for pp in self.bare_points_with_centre]
        self._points_wocs_ctwcc = [pp - self.centroid_with_centre for pp in self.bare_points_without_centre]
        self._points_wcs_ctwocc = [pp - self.centroid_without_centre for pp in self.bare_points_with_centre]
        self._points_wocs_ctwocc = [pp - self.centroid_without_centre for pp in self.bare_points_without_centre]

        self.centering_type = centering_type
        self.include_central_site_in_centroid = include_central_site_in_centroid
        self.bare_central_site = central_site
        if centering_type == 'standard':
            if len(bare_coords) < 5:
                if include_central_site_in_centroid:
                    raise ValueError("The center is the central site, no calculation of the centroid,"
                                     "variable include_central_site_in_centroid should be set to False")
                if central_site is None:
                    raise ValueError("Centering_type is central_site, the central site should be given")
                self.centre = np.array(central_site)
            else:
                total = np.zeros(3, np.float)
                for pp in bare_coords:
                    total += pp
                if include_central_site_in_centroid:
                    if central_site is None:
                        raise ValueError("The centroid includes the central site but no central site is given")
                    total += central_site
                    self.centre = total / (np.float(len(bare_coords)) + 1.0)
                else:
                    self.centre = total / np.float(len(bare_coords))
        elif centering_type == 'central_site':
            if include_central_site_in_centroid:
                raise ValueError("The center is the central site, no calculation of the centroid,"
                                 "variable include_central_site_in_centroid should be set to False")
            if central_site is None:
                raise ValueError("Centering_type is central_site, the central site should be given")
            self.centre = np.array(central_site)
        elif centering_type == 'centroid':
            total = np.zeros(3, np.float)
            for pp in bare_coords:
                total += pp
            if include_central_site_in_centroid:
                if central_site is None:
                    raise ValueError("The centroid includes the central site but no central site is given")
                total += central_site
                self.centre = total / (np.float(len(bare_coords)) + 1.0)
            else:
                self.centre = total / np.float(len(bare_coords))
        self._bare_coords = bare_coords
        self._coords = list()
        for ip, pp in enumerate(bare_coords):
            self._coords.append(np.array(pp) - self.centre)
        self.central_site = self.bare_central_site - self.centre
        self.coords = self._coords
        self.bare_coords = self._bare_coords

    def __str__(self):
        """
        String representation of the AbstractGeometry
        :return: String representation of the AbstractGeometry
        """
        outs = ['\nAbstract Geometry with {n} points :'.format(n=len(self.coords))]
        for pp in self.coords:
            outs.append('  {pp}'.format(pp=pp))
        if self.centering_type == 'standard':
            if self.include_central_site_in_centroid:
                outs.append('Points are referenced to the central site for coordination numbers < 5'
                            ' and to the centroid (calculated with the central site) for coordination'
                            ' numbers >= 5 : {c}\n'.format(c=self.centre))
            else:
                outs.append('Points are referenced to the central site for coordination numbers < 5'
                            ' and to the centroid (calculated without the central site) for coordination'
                            ' numbers >= 5 : {c}\n'.format(c=self.centre))
        elif self.centering_type == 'central_site':
            outs.append('Points are referenced to the central site : {c}\n'.format(c=self.centre))
        elif self.centering_type == 'centroid':
            if self.include_central_site_in_centroid:
                outs.append('Points are referenced to the centroid'
                            ' (calculated with the central site) :\n  {c}\n'.format(c=self.centre))
            else:
                outs.append('Points are referenced to the centroid'
                            ' (calculated without the central site) :\n  {c}\n'.format(c=self.centre))
        return '\n'.join(outs)

    @classmethod
    def from_cg(cls, cg, centering_type='standard',
                include_central_site_in_centroid=False):
        central_site = cg.get_central_site()
        bare_coords = [np.array(pt, np.float) for pt in cg.points]
        return cls(central_site=central_site, bare_coords=bare_coords, centering_type=centering_type,
                   include_central_site_in_centroid=include_central_site_in_centroid)

    def points_wcs_csc(self, permutation=None):
        if permutation is None:
            return self._points_wcs_csc
        perm = [0]
        perm.extend([pp + 1 for pp in permutation])
        return [self._points_wcs_csc[ii] for ii in perm]

    def points_wocs_csc(self, permutation=None):
        if permutation is None:
            return self._points_wocs_csc
        return [self._points_wocs_csc[ii] for ii in permutation]

    def points_wcs_ctwcc(self, permutation=None):
        if permutation is None:
            return self._points_wcs_ctwcc
        perm = [0]
        perm.extend([pp + 1 for pp in permutation])
        return [self._points_wcs_ctwcc[ii] for ii in perm]

    def points_wocs_ctwcc(self, permutation=None):
        if permutation is None:
            return self._points_wocs_ctwcc
        return [self._points_wocs_ctwcc[ii] for ii in permutation]

    def points_wcs_ctwocc(self, permutation=None):
        if permutation is None:
            return self._points_wcs_ctwocc
        perm = [0]
        perm.extend([pp + 1 for pp in permutation])
        return [self._points_wcs_ctwocc[ii] for ii in perm]

    def points_wocs_ctwocc(self, permutation=None):
        if permutation is None:
            return self._points_wocs_ctwocc
        return [self._points_wocs_ctwocc[ii] for ii in permutation]


def symmetry_measure(points_distorted, points_perfect):
    """
    Computes the continuous symmetry measure of the (distorted) set of points "points_distorted" with respect to the
    (perfect) set of points "points_perfect".
    :param points_distorted: List of points describing a given (distorted) polyhedron for which the symmetry measure
                             has to be computed with respect to the model polyhedron described by the list of points
                             "points_perfect".
    :param points_perfect: List of "perfect" points describing a given model polyhedron.
    :return: The continuous symmetry measure of the distorted polyhedron with respect to the perfect polyhedron
    """
    # When there is only one point, the symmetry measure is 0.0 by definition
    if len(points_distorted) == 1:
        return 0.0
    # Find the rotation matrix that aligns the distorted points to the perfect points in a least-square sense.
    rot = find_rotation(points_distorted=points_distorted,
                        points_perfect=points_perfect)
    # Find the scaling factor between the distorted points and the perfect points in a least-square sense.
    scaling_factor, rotated_coords = find_scaling_factor(points_distorted=points_distorted,
                                                         points_perfect=points_perfect,
                                                         rot=rot)
    # Compute the continuous symmetry measure [see Eq. 1 in Pinsky et al., Inorganic Chemistry 37, 5575 (1998)]
    num = 0.0
    denom = 0.0
    for ip, pp in enumerate(points_perfect):
        rotated_coords[ip] = scaling_factor * rotated_coords[ip]
        diff = (pp - rotated_coords[ip])
        num += np.sum(diff * diff)
        denom += np.sum(pp * pp)
    return num / denom * 100.0

def find_rotation(points_distorted, points_perfect):
    """
    This finds the rotation matrix that aligns the (distorted) set of points "points_distorted" with respect to the
    (perfect) set of points "points_perfect" in a least-square sense.
    :param points_distorted: List of points describing a given (distorted) polyhedron for which the rotation that
                             aligns these points in a least-square sense to the set of perfect points "points_perfect"
    :param points_perfect: List of "perfect" points describing a given model polyhedron.
    :return: The rotation matrix
    """
    isexact = True
    for ip, pp in enumerate(points_distorted):
        if not np.allclose(pp, points_perfect[ip]):
            isexact = False
            break
    if isexact:
        rot = np.eye(3)
        return rot
    H = np.zeros([3, 3], np.float)
    for ip, pp in enumerate(points_distorted):
        H += vectorsToMatrix(pp, points_perfect[ip])
    [U, S, Vt] = svd(H)
    rot = matrixMultiplication(transpose(Vt), transpose(U))
    return rot

def find_scaling_factor(points_distorted, points_perfect, rot):
    """
    This finds the scaling factor between the (distorted) set of points "points_distorted" and the
    (perfect) set of points "points_perfect" in a least-square sense.
    :param points_distorted: List of points describing a given (distorted) polyhedron for which the scaling factor has
                             to be obtained.
    :param points_perfect: List of "perfect" points describing a given model polyhedron.
    :param rot: The rotation matrix
    :return: The scaling factor between the two structures and the rotated set of (distorted) points.
    """
    rotated_coords = rotateCoords(points_distorted, rot)
    num = np.sum([np.dot(rc, points_perfect[ii]) for ii, rc in enumerate(rotated_coords)])
    denom = np.sum([np.dot(rc, rc) for rc in rotated_coords])
    return num / denom, rotated_coords


class LocalGeometryFinder(object):
    """
    Main class used to find the local environments in a structure
    """
    DEFAULT_BVA_DISTANCE_SCALE_FACTOR = 1.0
    BVA_DISTANCE_SCALE_FACTORS = {'experimental': 1.0, 'GGA_relaxed': 1.015, 'LDA_relaxed': 0.995}
    DEFAULT_SPG_ANALYZER_OPTIONS = {'symprec': 1e-3, 'angle_tolerance': 5}
    STRUCTURE_REFINEMENT_NONE = 'none'
    STRUCTURE_REFINEMENT_REFINED = 'refined'
    STRUCTURE_REFINEMENT_SYMMETRIZED = 'symmetrized'

    def __init__(self, permutations_safe_override=False, plane_ordering_override=True, debug_level=None,
                 plane_safe_permutations=False, logfile='chemenv_local_geometry_finder.log', only_symbols=None):
        """
        Constructor for the LocalGeometryFinder, initializes the list of coordination geometries
        :param permutations_safe_override: If set to True, all permutations are tested (very time-consuming for large
        coordination numbers!)
        :param plane_ordering_override: If set to False, the ordering of the points in the plane is disabled
        """
        self.cg = AllCoordinationGeometries(permutations_safe_override=permutations_safe_override,
                                            only_symbols=only_symbols)
        self.permutations_safe_override = permutations_safe_override
        self.plane_ordering_override = plane_ordering_override
        self.plane_safe_permutations = plane_safe_permutations
        self.setup_parameters(centering_type='centroid', include_central_site_in_centroid=True,
                              bva_distance_scale_factor=None, structure_refinement='refined')

    def setup_parameters(self, centering_type='standard', include_central_site_in_centroid=False,
                         bva_distance_scale_factor=None, structure_refinement=STRUCTURE_REFINEMENT_REFINED,
                         spg_analyzer_options=None):
        """
        Setup of the parameters for the coordination geometry finder. A reference point for the geometries has to be
        chosen. This can be the centroid of the structure (including or excluding the atom for which the coordination
        geometry is looked for) or the atom itself. In the 'standard' centering_type, the reference point is the central
        atom for coordination numbers 1, 2, 3 and 4 and the centroid for coordination numbers > 4.
        :param centering_type: Type of the reference point (centering) 'standard', 'centroid' or 'central_site'
        :param include_central_site_in_centroid: In case centering_type is 'centroid', the central site is included if
                                                 this value is set to True.
        :param bva_distance_scale_factor: Scaling factor for the bond valence analyzer (this might be different whether
                                          the structure is an experimental one, an LDA or a GGA relaxed one, or any
                                          other relaxation scheme (where under- or over-estimation of bond lengths
                                          is known).
        :param structure_refinement: Refinement of the structure. Can be "none", "refined" or "symmetrized".
        :param spg_analyzer_options: Options for the SpaceGroupAnalyzer (dictionary specifying "symprec"
                                     and "angle_tolerance". See pymatgen's SpaceGroupAnalyzer for more information.
        """
        self.centering_type = centering_type
        self.include_central_site_in_centroid = include_central_site_in_centroid
        if bva_distance_scale_factor is not None:
            self.bva_distance_scale_factor = bva_distance_scale_factor
        else:
            self.bva_distance_scale_factor = self.DEFAULT_BVA_DISTANCE_SCALE_FACTOR
        self.structure_refinement = structure_refinement
        if spg_analyzer_options is None:
            self.spg_analyzer_options = self.DEFAULT_SPG_ANALYZER_OPTIONS
        else:
            self.spg_analyzer_options = spg_analyzer_options

    def setup_parameter(self, parameter, value):
        """
        Setup of one specific parameter to the given value. The other parameters are unchanged. See setup_parameters
        method for the list of possible parameters
        :param parameter: Parameter to setup/update
        :param value: Value of the parameter
        """
        self.__dict__[parameter] = value

    def setup_structure(self, structure):
        """
        Sets up the structure for which the coordination geometries have to be identified. The structure is analyzed
        with the space group analyzer and a refined structure is used
        :param structure: A pymatgen Structure
        :param
        """
        self.initial_structure = structure.copy()
        if self.structure_refinement == self.STRUCTURE_REFINEMENT_NONE:
            self.structure = structure.copy()
            self.spg_analyzer = None
            self.symmetrized_structure = None
        else:
            self.spg_analyzer = SpacegroupAnalyzer(self.initial_structure,
                                                   symprec=self.spg_analyzer_options['symprec'],
                                                   angle_tolerance=self.spg_analyzer_options['angle_tolerance'])
            if self.structure_refinement == self.STRUCTURE_REFINEMENT_REFINED:
                self.structure = self.spg_analyzer.get_refined_structure()
                self.symmetrized_structure = None
            elif self.structure_refinement == self.STRUCTURE_REFINEMENT_SYMMETRIZED:
                self.structure = self.spg_analyzer.get_refined_structure()
                self.spg_analyzer_refined = SpacegroupAnalyzer(self.structure,
                                                               symprec=self.spg_analyzer_options['symprec'],
                                                               angle_tolerance=self.spg_analyzer_options
                                                               ['angle_tolerance'])
                self.symmetrized_structure = self.spg_analyzer_refined.get_symmetrized_structure()

    def get_structure(self):
        """
        Returns the pymatgen Structure that has been setup for the identification of geometries (the initial one
        might have been refined/symmetrized using the SpaceGroupAnalyzer).
        :return: The pymatgen Structure that has been setup for the identification of geometries (the initial one
        might have been refined/symmetrized using the SpaceGroupAnalyzer).
        """
        return self.structure

    def set_structure(self, lattice, species, coords, coords_are_cartesian):
        """
        Sets up the pymatgen structure for which the coordination geometries have to be identified starting from the
        lattice, the species and the coordinates
        :param lattice: The lattice of the structure
        :param species: The species on the sites
        :param coords: The coordinates of the sites
        :param coords_are_cartesian: If set to True, the coordinates are given in cartesian coordinates
        """
        self.setup_structure(Structure(lattice, species, coords, coords_are_cartesian))

    def compute_structure_environments_detailed_voronoi(self, excluded_atoms=None, only_atoms=None,
                                                        only_cations=True, only_indices=None,
                                                        source_structure_valence_fallback=False,
                                                        no_valence_exclude_atoms_fallback=None,
                                                        maximum_distance_factor=None,
                                                        minimum_angle_factor=None, max_cn=None):
        """
        Computes and returns the StructureEnvironments object containing all the information about the coordination
        environments in the structure
        :param excluded_atoms: Atoms for which the coordination geometries does not have to be identified
        :param only_atoms: If not set to None, atoms for which the coordination geometries have to be identified
        :return: The StructureEnvironments object containing all the information about the coordination
        environments in the structure
        """

        # Bond valence analysis to get approximated valences
        logging.info('Getting valences using BVAnalyzer')
        bva = BVAnalyzer(distance_scale_factor=self.bva_distance_scale_factor)
        self.info = {}
        try:
            self.bva_valences = bva.get_valences(self.structure)
            self.info['valences'] = 'bva'
        except:
            self.bva_valences = 'undefined'
            self.info['valences'] = 'undefined'
        self.valences = self.bva_valences

        # Get a list of indices of unequivalent sites from the initial structure
        if (self.structure_refinement == self.STRUCTURE_REFINEMENT_SYMMETRIZED and
                    len(self.symmetrized_structure.equivalent_sites) > 0):
            logging.info('Symmetrizing and refining structure')
            indices = []
            ind_eqsites_found = []
            self.equivalent_sites = self.symmetrized_structure.equivalent_sites
            self.struct_sites_to_irreducible_site_list_map = [-1] * len(self.structure)
            self.sites_map = [-1] * len(self.structure)
            eqsite_found = [-1] * len(self.symmetrized_structure.equivalent_sites)
            for isite, site in enumerate(self.structure):
                for ieqsites, eqsites in enumerate(self.symmetrized_structure.equivalent_sites):
                    if site in eqsites:
                        self.struct_sites_to_irreducible_site_list_map[isite] = ieqsites
                        if ieqsites not in ind_eqsites_found:
                            indices.append(isite)
                            ind_eqsites_found.append(ieqsites)
                            myieqsite = isite
                            eqsite_found[ieqsites] = myieqsite
                        else:
                            myieqsite = eqsite_found[ieqsites]
                self.sites_map[isite] = myieqsite
        else:
            self.equivalent_sites = [[site] for site in self.structure]
            self.struct_sites_to_irreducible_site_list_map = list(range(len(self.structure)))
            self.sites_map = list(range(len(self.structure)))
            indices = list(range(len(self.structure)))

        if source_structure_valence_fallback and self.bva_valences == 'undefined':
            dummyspoccu = self.structure[0].species_and_occu
            ok = False
            if isinstance(dummyspoccu.keys()[0], Specie):
                self.valences = []
                for isite, site in enumerate(self.structure):
                    oxi_state = sum([frac * sp.oxi_state for sp, frac in site.species_and_occu.items()])
                    self.valences.append(oxi_state)
                if any([val != 0 for val in self.valences]):
                    ok = True
                else:
                    self.valences = 'undefined'
            if not ok:
                if no_valence_exclude_atoms_fallback is not None:
                    if excluded_atoms is None:
                        excluded_atoms = no_valence_exclude_atoms_fallback
                    else:
                        for atom in no_valence_exclude_atoms_fallback:
                            if atom not in excluded_atoms:
                                excluded_atoms.append(atom)

        # Get list of unequivalent sites with valence >= 0
        if only_cations and self.valences != 'undefined':
            sites_indices = [isite for isite in indices if self.valences[isite] >= 0]
        else:
            sites_indices = [isite for isite in indices]

        # Include atoms that are in the list of "only_atoms" if it is provided
        if only_atoms is not None:
            sites_indices = [isite for isite in sites_indices
                             if any([at in [sp.symbol for sp in self.structure[isite].species_and_occu]
                                     for at in only_atoms])]

        # Exclude atoms that are in the list of excluded atoms
        if excluded_atoms:
            sites_indices = [isite for isite in sites_indices
                             if not any([at in [sp.symbol for sp in self.structure[isite].species_and_occu]
                                         for at in excluded_atoms])]

        if only_indices is not None:
            sites_indices = [isite for isite in indices if isite in only_indices]

        # Get the VoronoiContainer for this list of unequivalent sites with valence >= 0
        logging.info('Getting DetailedVoronoiContainer')
        self.detailed_voronoi = DetailedVoronoiContainer(self.structure, isites=sites_indices,
                                                         valences=self.valences,
                                                         maximum_distance_factor=maximum_distance_factor,
                                                         minimum_angle_factor=minimum_angle_factor)
        logging.info('DetailedVoronoiContainer has been set up')

        ce_list = []
        skipped = []
        logging.info('Computing structure environments')
        tse1 = time.clock()
        for isite in range(len(self.structure)):
            if isite not in sites_indices:
                logging.info(' ... in site #{:d} ({}) : skipped'.format(isite, self.structure[isite].species_string))
                skipped.append(isite)
                ce_list.append(None)
                continue
            logging.info(' ... in site #{:d} ({})'.format(isite, self.structure[isite].species_string))
            t1 = time.clock()
            coords = self.detailed_voronoi.unique_coordinations(isite)

            ce_dict = {}
            for cn in coords:
                if max_cn is not None and cn > max_cn:
                    continue
                ce_dict[cn] = []
                for i_nlist, nlist_tuple in enumerate(coords[cn]):
                    neighb_list = nlist_tuple[0]
                    ce = ChemicalEnvironments()
                    mycoords = [st.coords for st in neighb_list]
                    self.setup_local_geometry(isite, coords=mycoords)
                    cncgsm = self.get_coordination_symmetry_measures()
                    for cg in cncgsm:

                        other_csms = {'csm_wocs_ctwocc': cncgsm[cg]['csm_wocs_ctwocc'],
                                      'csm_wocs_ctwcc': cncgsm[cg]['csm_wocs_ctwcc'],
                                      'csm_wocs_csc': cncgsm[cg]['csm_wocs_csc'],
                                      'csm_wcs_ctwocc': cncgsm[cg]['csm_wcs_ctwocc'],
                                      'csm_wcs_ctwcc': cncgsm[cg]['csm_wcs_ctwcc'],
                                      'csm_wcs_csc': cncgsm[cg]['csm_wcs_csc'],}
                        ce.add_coord_geom(cg, cncgsm[cg]['csm'], algo=cncgsm[cg]['algo'],
                                          permutation=cncgsm[cg]['indices'],
                                          local2perfect_map=cncgsm[cg]['local2perfect_map'],
                                          perfect2local_map=cncgsm[cg]['perfect2local_map'],
                                          detailed_voronoi_index={'cn': cn, 'index': i_nlist},
                                          other_symmetry_measures=other_csms
                                          )
                    ce_dict[cn].append(ce)
            t2 = time.clock()
            logging.info('    ... computed in {:.2f} seconds'.format(t2-t1))
            ce_list.append(ce_dict)
        tse2 = time.clock()
        logging.info('Structure environments computed in {:.2f} seconds'.format(tse2-tse1))
        return StructureEnvironments(self.detailed_voronoi, self.valences, self.sites_map, self.equivalent_sites,
                                     ce_list, self.structure)

    def setup_local_geometry(self, isite, coords):
        """
        Sets up the AbstractGeometry for the local geometry of site with index isite.
        :param isite: Index of the site for which the local geometry has to be set up
        :param coords: The coordinates of the (local) neighbors
        """
        self.local_geometry = AbstractGeometry(central_site=self.structure.cart_coords[isite],
                                               bare_coords=coords,
                                               centering_type=self.centering_type,
                                               include_central_site_in_centroid=
                                               self.include_central_site_in_centroid)

    def setup_test_perfect_environment(self, symbol, randomness=False, max_random_dist=0.1,
                                       symbol_type='mp_symbol', indices='RANDOM',
                                       random_translation=False, random_rotation=False, random_scale=False):
        if symbol_type == 'IUPAC':
            cg = self.cg.get_geometry_from_IUPAC_symbol(symbol)
        elif symbol_type == 'MP' or symbol_type == 'mp_symbol':
            cg = self.cg.get_geometry_from_mp_symbol(symbol)
        else:
            raise ValueError('Wrong mp_symbol to setup coordination geometry')
        neighb_coords = []
        if randomness:
            rv = np.random.random_sample(3)
            while norm(rv) > 1.0:
                rv = np.random.random_sample(3)
            coords = [np.zeros(3, np.float) + max_random_dist * rv]
            for pp in cg.points:
                rv = np.random.random_sample(3)
                while norm(rv) > 1.0:
                    rv = np.random.random_sample(3)
                neighb_coords.append(np.array(pp) + max_random_dist * rv)
        else:
            coords = [np.zeros(3, np.float)]
            for pp in cg.points:
                neighb_coords.append(np.array(pp))
        if indices == 'RANDOM':
            shuffle(neighb_coords)
        elif indices == 'ORDERED':
            pass
        else:
            neighb_coords = [neighb_coords[ii] for ii in indices]

        if random_scale:
            scale = 0.1*np.random.random_sample() + 0.95
            coords = [scale * cc for cc in coords]
            neighb_coords = [scale * cc for cc in neighb_coords]
        if random_rotation:
            uu = np.random.random_sample(3) + 0.1
            uu = uu / norm(uu)
            theta = np.pi * np.random.random_sample()
            cc = np.cos(theta)
            ss = np.sin(theta)
            ux = uu[0]
            uy = uu[1]
            uz = uu[2]
            RR = np.matrix([[ux*ux+(1.0-ux*ux)*cc, ux*uy*(1.0-cc)-uz*ss, ux*uz*(1.0-cc)+uy*ss],
                            [ux*uy*(1.0-cc)+uz*ss, uy*uy+(1.0-uy*uy)*cc, uy*uz*(1.0-cc)-ux*ss],
                            [ux*uz*(1.0-cc)-uy*ss, uy*uz*(1.0-cc)+ux*ss, uz*uz+(1.0-uz*uz)*cc]])
            newcoords = []
            for cc in coords:
                newcc = RR * np.matrix(cc).T
                newcoords.append(newcc.getA1())
            coords = newcoords
            newcoords = []
            for cc in neighb_coords:
                newcc = RR * np.matrix(cc).T
                newcoords.append(newcc.getA1())
            neighb_coords = newcoords
        if random_translation:
            translation = 10.0 * (2.0*np.random.random_sample(3)-1.0)
            coords = [cc + translation for cc in coords]
            neighb_coords = [cc + translation for cc in neighb_coords]
        coords.extend(neighb_coords)
        myspecies = ["O"] * (len(coords))
        myspecies[0] = "Cu"

        amin = np.min([cc[0] for cc in coords])
        amax = np.max([cc[0] for cc in coords])
        bmin = np.min([cc[1] for cc in coords])
        bmax = np.max([cc[1] for cc in coords])
        cmin = np.min([cc[2] for cc in coords])
        cmax = np.max([cc[2] for cc in coords])

        factor = 5.0
        aa = factor * max([amax - amin, bmax - bmin, cmax - cmin])

        lattice = Lattice.cubic(a=aa)
        structure = Structure(lattice=lattice, species=myspecies, coords=coords,
                              to_unit_cell=False, coords_are_cartesian=True)

        self.setup_structure(structure=structure)
        self.setup_local_geometry(isite=0, coords=neighb_coords)
        self.perfect_geometry = AbstractGeometry.from_cg(cg=cg)

    def setup_random_structure(self, coordination):
        """
        Sets up a purely random structure with a given coordination.
        :param coordination: coordination number for the random structure
        """
        aa = 0.4
        bb = -0.2
        coords = list()
        for ii in range(coordination + 1):
            coords.append(aa * np.random.random_sample(3, ) + bb)
        self.set_structure(lattice=np.array([[10, 0, 0], [0, 10, 0], [0, 0, 10]], np.float),
                           species=["Si"] * (coordination + 1),
                           coords=coords,
                           coords_are_cartesian=False)
        self.setup_random_indices_local_geometry(coordination)

    def setup_random_indices_local_geometry(self, coordination):
        """
        Sets up random indices for the local geometry, for testing purposes
        :param coordination: coordination of the local geometry
        """
        self.icentral_site = 0
        self.indices = list(range(1, coordination + 1))
        np.random.shuffle(self.indices)

    def setup_ordered_indices_local_geometry(self, coordination):
        """
        Sets up ordered indices for the local geometry, for testing purposes
        :param coordination: coordination of the local geometry
        """
        self.icentral_site = 0
        self.indices = list(range(1, coordination + 1))

    def setup_explicit_indices_local_geometry(self, explicit_indices):
        """
        Sets up explicit indices for the local geometry, for testing purposes
        :param explicit_indices: explicit indices for the neighbors (set of numbers
        from 0 to CN-1 in a given order)
        """
        self.icentral_site = 0
        self.indices = [ii+1 for ii in explicit_indices]

    def get_coordination_symmetry_measures(self, only_minimum=True, all_csms=True):
        """
        Returns the continuous symmetry measures of the current local geometry in a dictionary.
        :return: the continuous symmetry measures of the current local geometry in a dictionary.
        """
        test_geometries = self.cg.get_implemented_geometries(len(self.local_geometry.coords))
        result_dict = {}
        for geometry in test_geometries:
            self.perfect_geometry = AbstractGeometry.from_cg(cg=geometry,
                                                             centering_type=self.centering_type,
                                                             include_central_site_in_centroid=
                                                             self.include_central_site_in_centroid)
            points_perfect = self.perfect_geometry.points_wocs_ctwocc()
            cgsm = self.coordination_geometry_symmetry_measures(geometry, points_perfect=points_perfect)
            result, permutations, algos, local2perfect_maps, perfect2local_maps = cgsm
            if only_minimum:
                if len(result) > 0:
                    imin = np.argmin(result)
                    if geometry.algorithms is not None:
                        algo = algos[imin]
                    else:
                        algo = algos
                    result_dict[geometry.mp_symbol] = {'csm': result[imin], 'indices': permutations[imin],
                                                       'algo': algo,
                                                       'local2perfect_map': local2perfect_maps[imin],
                                                       'perfect2local_map': perfect2local_maps[imin]}
                    if all_csms:
                        permutation = permutations[imin]
                        # Without central site, centered on the centroid (centroid does not include the central site)
                        result_dict[geometry.mp_symbol]['csm_wocs_ctwocc'] = result[imin]
                        # Without central site, centered on the centroid (centroid includes the central site)
                        pdist = self.local_geometry.points_wocs_ctwcc(permutation=permutation)
                        pperf = self.perfect_geometry.points_wocs_ctwcc()
                        csm = symmetry_measure(points_distorted=pdist, points_perfect=pperf)
                        result_dict[geometry.mp_symbol]['csm_wocs_ctwcc'] = csm
                        # Without central site, centered on the central site
                        pdist = self.local_geometry.points_wocs_csc(permutation=permutation)
                        pperf = self.perfect_geometry.points_wocs_csc()
                        csm = symmetry_measure(points_distorted=pdist, points_perfect=pperf)
                        result_dict[geometry.mp_symbol]['csm_wocs_csc'] = csm
                        # With central site, centered on the centroid (centroid does not include the central site)
                        pdist = self.local_geometry.points_wcs_ctwocc(permutation=permutation)
                        pperf = self.perfect_geometry.points_wcs_ctwocc()
                        csm = symmetry_measure(points_distorted=pdist, points_perfect=pperf)
                        result_dict[geometry.mp_symbol]['csm_wcs_ctwocc'] = csm
                        # With central site, centered on the centroid (centroid includes the central site)
                        pdist = self.local_geometry.points_wcs_ctwcc(permutation=permutation)
                        pperf = self.perfect_geometry.points_wcs_ctwcc()
                        csm = symmetry_measure(points_distorted=pdist, points_perfect=pperf)
                        result_dict[geometry.mp_symbol]['csm_wcs_ctwcc'] = csm
                        # With central site, centered on the central site

                        pdist = self.local_geometry.points_wcs_csc(permutation=permutation)
                        pperf = self.perfect_geometry.points_wcs_csc()

                        csm = symmetry_measure(points_distorted=pdist, points_perfect=pperf)
                        result_dict[geometry.mp_symbol]['csm_wcs_csc'] = csm

            else:
                result_dict[geometry.mp_symbol] = {'csm': result, 'indices': permutations, 'algo': algos,
                                                   'local2perfect_map': local2perfect_maps,
                                                   'perfect2local_map': perfect2local_maps}
        return result_dict

    def coordination_geometry_symmetry_measures(self, coordination_geometry, tested_permutations=False,
                                                points_perfect=None):
        """
        Returns the symmetry measures of a given coordination_geometry for a set of permutations depending on
        the permutation setup. Depending on the parameters of the LocalGeometryFinder and on the coordination
         geometry, different methods are called.
        :param coordination_geometry: Coordination geometry for which the symmetry measures are looked for
        :return: the symmetry measures of a given coordination_geometry for a set of permutations
        :raise: NotImplementedError if the permutation_setup does not exists
        """
        if tested_permutations:
            tested_permutations = set()
        if self.permutations_safe_override:
            return self.coordination_geometry_symmetry_measures_safe_override(coordination_geometry)
        csms = []
        permutations = []
        algos = []
        local2perfect_maps = []
        perfect2local_maps = []
        for algo in coordination_geometry.algorithms:
            if algo.algorithm_type == EXPLICIT_PERMUTATIONS:
                return self.coordination_geometry_symmetry_measures_standard(coordination_geometry, algo,
                                                                             points_perfect=points_perfect)
            if algo.algorithm_type == SEPARATION_PLANE:
                cgsm = self.coordination_geometry_symmetry_measures_separation_plane(coordination_geometry,
                                                                                     algo,
                                                                                     tested_permutations=tested_permutations,
                                                                                     points_perfect=points_perfect)
                csm, perm, algo, local2perfect_map, perfect2local_map = cgsm

                csms.extend(csm)
                permutations.extend(perm)
                algos.extend(algo)
                local2perfect_maps.extend(local2perfect_map)
                perfect2local_maps.extend(perfect2local_map)
        return csms, permutations, algos, local2perfect_maps, perfect2local_maps

    def coordination_geometry_symmetry_measures_safe_override(self, coordination_geometry):
        """
        Returns the symmetry measures for a set of permutations (whose setup depends on the coordination geometry)
        for the coordination geometry "coordination_geometry". Standard implementatison looking for the symmetry
        measures of each permutation

        :param coordination_geometry: The coordination geometry to be investigated
        :return: The symmetry measures for the given coordination geometry for each permutation investigated
        """
        permutations_symmetry_measures = np.zeros(coordination_geometry.number_of_permutations, np.float)
        permutations = list()
        iperm = 0
        for perm in itertools.permutations(list(range(coordination_geometry.coordination_number))):
            permutations.append(np.argsort(perm))
            bare_coords = list()
            for ii in perm:
                bare_coords.append(coordination_geometry.points[ii])
            perfect_geometry = AbstractGeometry(central_site=coordination_geometry.get_central_site(),
                                                bare_coords=bare_coords,
                                                centering_type=self.centering_type,
                                                include_central_site_in_centroid=
                                                self.include_central_site_in_centroid)
            permutations_symmetry_measures[iperm] = self.symmetry_measure_newpmg(self.local_geometry, perfect_geometry)
            iperm += 1
        return permutations_symmetry_measures, permutations, 'SAFE'

    def coordination_geometry_symmetry_measures_standard(self, coordination_geometry, algo, points_perfect=None):
        """
        Returns the symmetry measures for a set of permutations (whose setup depends on the coordination geometry)
        for the coordination geometry "coordination_geometry". Standard implementation looking for the symmetry
        measures of each permutation

        :param coordination_geometry: The coordination geometry to be investigated
        :return: The symmetry measures for the given coordination geometry for each permutation investigated
        """
        permutations_symmetry_measures = np.zeros(len(algo.permutations), np.float)
        permutations = list()
        algos = list()
        local2perfect_maps = list()
        perfect2local_maps = list()
        for iperm, perm in enumerate(algo.permutations):

            local2perfect_map = {}
            perfect2local_map = {}
            permutations.append(perm)
            for iperfect, ii in enumerate(perm):
                perfect2local_map[iperfect] = ii
                local2perfect_map[ii] = iperfect
            local2perfect_maps.append(local2perfect_map)
            perfect2local_maps.append(perfect2local_map)

            points_distorted = self.local_geometry.points_wocs_ctwocc(permutation=perm)

            csm = symmetry_measure(points_distorted=points_distorted,
                                   points_perfect=points_perfect)

            permutations_symmetry_measures[iperm] = csm
            algos.append(str(algo))
        return permutations_symmetry_measures, permutations, algos, local2perfect_maps, perfect2local_maps

    def coordination_geometry_symmetry_measures_separation_plane(self, coordination_geometry,
                                                                 separation_plane_algo,
                                                                 testing=False,
                                                                 tested_permutations=False,
                                                                 points_perfect=None):


        """
        Returns the symmetry measures of the given coordination geometry "coordination_geometry" using separation
        planes to reduce the complexity of the system. Caller to the refined 2POINTS, 3POINTS and other ...
        :param coordination_geometry: The coordination geometry to be investigated
        :return: The symmetry measures for the given coordination geometry for each plane and permutation investigated
        """
        permutations = list()
        permutations_symmetry_measures = list()
        plane_separations = list()
        algos = list()
        perfect2local_maps = list()
        local2perfect_maps = list()
        if testing:
            separation_permutations = list()
        nplanes = 0
        for npoints in range(separation_plane_algo.minimum_number_of_points,
                             min(separation_plane_algo.maximum_number_of_points, 4) + 1):
            for points_combination in itertools.combinations(self.local_geometry.coords, npoints):
                if npoints == 2:
                    if collinear(points_combination[0], points_combination[1],
                                 self.local_geometry.central_site, tolerance=0.25):
                        continue
                    plane = Plane.from_3points(points_combination[0], points_combination[1],
                                               self.local_geometry.central_site)
                elif npoints == 3:
                    if collinear(points_combination[0], points_combination[1], points_combination[2], tolerance=0.25):
                        continue
                    plane = Plane.from_3points(points_combination[0], points_combination[1], points_combination[2])
                elif npoints > 3:
                    plane = Plane.from_npoints(points_combination, best_fit='least_square_distance')
                else:
                    raise ValueError('Wrong number of points to initialize separation plane')
                cgsm = self._cg_csm_separation_plane(coordination_geometry=coordination_geometry,
                                                     sepplane=separation_plane_algo,
                                                     local_plane=plane,
                                                     plane_separations=plane_separations,
                                                     dist_tolerances=DIST_TOLERANCES,
                                                     testing=testing,
                                                     tested_permutations=tested_permutations,
                                                     points_perfect=points_perfect)
                csm, perm, algo = cgsm[0], cgsm[1], cgsm[2]

                if csm is not None:
                    permutations_symmetry_measures.extend(csm)
                    permutations.extend(perm)
                    for thisperm in perm:
                        p2l = {}
                        l2p = {}
                        for i_p, pp in enumerate(thisperm):
                            p2l[i_p] = pp
                            l2p[pp] = i_p
                        perfect2local_maps.append(p2l)
                        local2perfect_maps.append(l2p)
                    algos.extend(algo)
                    if testing:
                        separation_permutations.extend(cgsm[3])
                    nplanes += 1
            if nplanes > 0:
                break
        if nplanes == 0:
            return self.coordination_geometry_symmetry_measures_fallback_random(coordination_geometry,
                                                                                points_perfect=points_perfect)
        if testing:
            return np.array(permutations_symmetry_measures, np.float), permutations, separation_permutations
        return np.array(permutations_symmetry_measures, np.float), permutations, algos, local2perfect_maps, perfect2local_maps

    def _cg_csm_separation_plane(self, coordination_geometry, sepplane, local_plane,
                                 plane_separations, dist_tolerances=DIST_TOLERANCES,
                                 testing=False, tested_permutations=False, points_perfect=None):
        argref_separation = sepplane.argsorted_ref_separation_perm
        plane_found = False
        for dist_tolerance in dist_tolerances:
            permutations = []
            permutations_symmetry_measures = []
            if testing:
                separation_permutations = []
            algo = 'NOT_FOUND'
            separation = local_plane.indices_separate(self.local_geometry._coords, dist_tolerance)
            # Do not consider planes leading to the same separation indices
            separation = sort_separation(separation)

            if separation_in_list(separation, plane_separations):
                continue
            # Do not consider a separation which does not follow the reference separation of the perfect
            # coordination geometry
            if len(separation[1]) != len(sepplane.plane_points):
                continue
            if len(separation[0]) == len(sepplane.point_groups[0]):
                this_separation = separation
                plane_separations.append(this_separation)
            elif len(separation[0]) == len(sepplane.point_groups[1]):
                this_separation = [list(separation[2]), list(separation[1]), list(separation[0])]
                plane_separations.append(this_separation)
            else:
                continue

            if sepplane.ordered_plane:
                inp = [pp for ip, pp in enumerate(self.local_geometry._coords) if ip in this_separation[1]]

                if sepplane.ordered_point_groups[0]:
                    pp_s0 = [pp for ip, pp in enumerate(self.local_geometry._coords) if ip in this_separation[0]]
                    ordind_s0 = local_plane.project_and_to2dim_ordered_indices(pp_s0)
                    sep0 = [this_separation[0][ii] for ii in ordind_s0]
                else:
                    sep0 = list(this_separation[0])
                if sepplane.ordered_point_groups[1]:
                    pp_s2 = [pp for ip, pp in enumerate(self.local_geometry._coords) if ip in this_separation[2]]
                    ordind_s2 = local_plane.project_and_to2dim_ordered_indices(pp_s2)
                    sep2 = [this_separation[2][ii] for ii in ordind_s2]
                else:
                    sep2 = list(this_separation[2])
                separation_perm = list(sep0)
                ordind = local_plane.project_and_to2dim_ordered_indices(inp)
                separation_perm.extend([this_separation[1][ii] for ii in ordind])
                algo = 'SEPARATION_PLANE_2POINTS_ORDERED'
                separation_perm.extend(sep2)
            else:
                separation_perm = list(this_separation[0])
                separation_perm.extend(this_separation[1])
                algo = 'SEPARATION_PLANE_2POINTS'
                separation_perm.extend(this_separation[2])
            if self.plane_safe_permutations:
                sep_perms = sepplane.safe_separation_permutations(ordered_plane=sepplane.ordered_plane,
                                                                  ordered_point_groups=sepplane.ordered_point_groups)
            else:
                sep_perms = sepplane.permutations

            plane_found = True

            for i_sep_perm, sep_perm in enumerate(sep_perms):
                perm1 = [separation_perm[ii] for ii in sep_perm]
                pp = [perm1[ii] for ii in argref_separation]
                # Skip permutations that have already been performed
                if tested_permutations != False and coordination_geometry.equivalent_indices is not None:
                    tuple_ref_perm = coordination_geometry.ref_permutation(pp)
                    if tuple_ref_perm in tested_permutations:
                        continue
                    tested_permutations.add(tuple_ref_perm)

                permutations.append(pp)
                if testing:
                    separation_permutations.append(sep_perm)

                points_distorted = self.local_geometry.points_wocs_ctwocc(permutation=pp)

                csm = symmetry_measure(points_distorted=points_distorted, points_perfect=points_perfect)

                permutations_symmetry_measures.append(csm)
            if plane_found:
                break
        if len(permutations_symmetry_measures) > 0:
            if testing:
                return permutations_symmetry_measures, permutations, algo, separation_permutations
            else:
                return permutations_symmetry_measures, permutations, [sepplane.algorithm_type] * len(permutations)
        else:
            if plane_found:
                return permutations_symmetry_measures, permutations, []
            else:
                return None, None, None

    def coordination_geometry_symmetry_measures_fallback_random(self, coordination_geometry, NRANDOM=200,
                                                                points_perfect=None):
        """
        Returns the symmetry measures for a random set of permutations for the coordination geometry
        "coordination_geometry". Fallback implementation for the plane separation algorithms measures
        of each permutation

        :param coordination_geometry: The coordination geometry to be investigated
        :param NRANDOM: Number of random permutations to be tested
        :return: The symmetry measures for the given coordination geometry for each permutation investigated
        """
        permutations_symmetry_measures = np.zeros(NRANDOM, np.float)
        permutations = list()
        algos = list()
        perfect2local_maps = list()
        local2perfect_maps = list()
        for iperm in range(NRANDOM):
            perm = np.random.permutation(coordination_geometry.coordination_number)
            permutations.append(perm)
            p2l = {}
            l2p = {}
            for i_p, pp in enumerate(perm):
                p2l[i_p] = pp
                l2p[pp] = i_p
            perfect2local_maps.append(p2l)
            local2perfect_maps.append(l2p)

            points_distorted = self.local_geometry.points_wocs_ctwocc(permutation=perm)
            csm = symmetry_measure(points_distorted=points_distorted, points_perfect=points_perfect)
            permutations_symmetry_measures[iperm] = csm
            algos.append('APPROXIMATE_FALLBACK')
        return permutations_symmetry_measures, permutations, algos, local2perfect_maps, perfect2local_maps