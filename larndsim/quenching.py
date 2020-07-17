"""
Module to implement the quenching of the ionized electrons
through the detector
"""

from math import log, isnan
import numba as nb
from . import consts


@nb.njit(parallel=True)
def Quench(tracks, col):
    """
    CPU Quenching Kernel function
    """
    for index in nb.prange(tracks.shape[0]):
        recomb = log(consts.alpha
                     + consts.beta * tracks[index, col["dEdx"]]
                     / (consts.beta * tracks[index, col["dEdx"]]))

        if recomb <= 0 or isnan(recomb):
            recomb = 0

        tracks[index, col["NElectrons"]] = recomb * tracks[index, col["dE"]] * consts.MeVToElectrons
