"""
Module that simulates the scattering of photons throughout the detector from the
location of the edep to the location of each photodetector
"""

import numpy as np

from .consts import light, detector

def get_voxel(pos, itpc):
    """
    Finds and returns the indices of the voxel in which the edep occurs.
    Args:
        pos (:obj:`numpy.ndarray`): list of x, y, z coordinates within a generic TPC volume
        itpc (int): index of the tpc corresponding to this position (calculated in drift)
    Returns:
        (tuple) indices (in x, y, z dimensions) of the voxel containing the input position
    """

    this_tpc_borders = detector.TPC_BORDERS[itpc]

    # If we are in an "odd" TPC, that is, if the index of
    # the tpc is an odd number, we need to rotate x
    # this is to preserve the "left/right"-ness of the optical channels
    # with respect to the anode plane
    is_even = this_tpc_borders[2][1] > this_tpc_borders[2][0]

    # Assigns tpc borders to variables
    # +- 2e-2 mimics the logic used in drifting.py to prevent event
    # voxel indicies from being located outside the LUT
    xMin = this_tpc_borders[0][0] - 2e-2
    xMax = this_tpc_borders[0][1] + 2e-2
    yMin = this_tpc_borders[1][0] - 2e-2
    yMax = this_tpc_borders[1][1] + 2e-2
    zMin = this_tpc_borders[2][0] - 2e-2
    zMax = this_tpc_borders[2][1] + 2e-2

    # Determines which voxel the event takes place in
    # based on the fractional dstance the event takes place in the volume
    # for the x, y, and z dimensions
    if is_even:
        i = int((pos[0] - xMin)/(xMax - xMin) * light.LUT_VOX_DIV[0])
    else:
        # if is_even, is false we measure i from the xMax side
        # rather than the xMin side as means of rotating the x component
        i = int((xMax - pos[0])/(xMax - xMin) * light.LUT_VOX_DIV[0])
    j = int((pos[1] - yMin)/(yMax - yMin) * light.LUT_VOX_DIV[1])
    k = int((pos[2] - zMin)/(zMax - zMin) * light.LUT_VOX_DIV[2])

    return i,j,k

def calculate_light_incidence(tracks, lut_path, light_incidence):
    """
    Simulates the number of photons read by each optical channel depending on
        where the edep occurs as well as the time it takes for a photon to reach the
        nearest photomultiplier tube (the "fastest" photon)
    Args:
        tracks (:obj:`numpy.ndarray`): track array containing edep segments, positions are used for lookup
        lut_path (str): filename of numpy array (.npy) containing light calculation
        light_dep (:obj:`numpy.ndarray`): 1-Dimensional array containing number of photons produced
            in each edep segment.
        light_incidence (:obj:`numpy.ndarray`): to contain the result of light incidence calculation.
            this array has dimension (n_tracks, n_optical_channels) and each entry
            is a structure of type (n_photons_det (float32), t0_det (float32))
            these correspond to the number detected in each channel (n_photons_edep*visibility),
            and the time of earliest arrival at that channel.
    """

    # Loads in LUT file
    np_lut = np.load(lut_path)

    # Defines variables of global position.
    # Currently using the average between the start and end positions of the edep
    x = tracks['x']
    y = tracks['y']
    z = tracks['z']

    # Determines number of edeps
    nEdepSegments = tracks.shape[0]

    # Loop edep positions
    for edepInd in range(nEdepSegments):

        # Global position
        pos = (np.array((x[edepInd],y[edepInd],z[edepInd])))

        # Defining number of produced photons from quencing.py
        n_photons = tracks['n_photons'][edepInd]

        # Identifies which tpc event takes place in
        itpc = tracks["pixel_plane"][edepInd]

        # Voxel containing LUT position
        voxel = get_voxel(pos, itpc)

        # Calls data from voxel
        lut_vox = np_lut[voxel[0], voxel[1], voxel[2],:,:]

        # Indices corresponding to the channels in a given tpc
        output_channels = np.arange(light.N_OP_CHANNEL) + int(itpc*light.N_OP_CHANNEL)

        # Calls visibility data for the voxel
        vis_dat = lut_vox[:,0]

        # Calls T1 data for the voxel
        T1_dat = lut_vox[:,1]

        # Assigns the LUT data to the light_incidence array
        for outputInd, eff, vis, t1 in zip(output_channels, light.OP_CHANNEL_EFFICIENCY, vis_dat, T1_dat):
            light_incidence[edepInd, outputInd] = (eff*vis*n_photons, t1)
