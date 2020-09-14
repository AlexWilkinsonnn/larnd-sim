"""
Module to implement the quenching of the ionized electrons
through the detector
"""

from math import log, isnan
from numba import cuda

from . import consts
from . import indeces as i

import logging
logging.basicConfig()
logger = logging.getLogger('quenching')
logger.setLevel(logging.INFO)
logger.info("QUENCHING MODULE PARAMETERS")
logger.info("""TPC parameters
Electric field magnitude: %g kV/cm
Liquid argon density: %g g/cm^3
Box alpha parameter: %g
Box beta parameter: %g cm/MeV
Birks A_b parameter: %g
Birks k_b parameter: %g g/cm^2/MeV
""" % (consts.eField, consts.lArDensity,
       consts.alpha, consts.beta,
       consts.Ab, consts.kb))

@cuda.jit
def quench(tracks, mode):
    """
    This CUDA kernel takes as input an array of track segments and calculates
    the number of electrons that reach the anode plane after recombination.
    It is possible to pick among two models: Box (Baller, 2013 JINST 8 P08005) or
    Birks (Amoruso, et al NIM A 523 (2004) 275).

    Args:
        tracks (:obj:`numpy.ndarray`): array containing the tracks segment information
        mode (int): recombination model.
    """
    itrk = cuda.grid(1)

    if itrk < tracks.shape[0]:
        dEdx = tracks[itrk][i.dEdx]
        dE = tracks[itrk][i.dE]

        recomb = 0
        if mode == consts.box:
            # Baller, 2013 JINST 8 P08005
            csi = consts.beta * dEdx / (consts.eField * consts.lArDensity)
            recomb = max(0, log(consts.alpha + csi)/csi)
        elif mode == consts.birks:
            # Amoruso, et al NIM A 523 (2004) 275
            recomb = consts.Ab / (1 + consts.kb * dEdx / (consts.eField * consts.lArDensity))
        else:
            raise ValueError("Invalid recombination mode: must be 'box' or 'birks'")

        if isnan(recomb):
            raise RuntimeError("Invalid recombination value")

        tracks[itrk][i.n_electrons] = recomb * dE * consts.MeVToElectrons
