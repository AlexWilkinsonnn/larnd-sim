"""
Module that calculates the current induced by edep-sim track segments
on the pixels
"""

import numba as nb
import numpy as np
import skimage.draw

from math import pi, ceil, sqrt, erf, exp
from .consts import *


@nb.njit(fastmath=True)
def current_response(t, A=1, B=5, t0=0):
    """Current response parametrization"""
    result = A * np.exp((t - t0) / B)
    result[t > t0] = 0
    return result

@nb.njit(fastmath=True)
def slice_coordinates(point, padding, slice_size):
    xl = point[0]
    yl = point[1]
    xx = np.linspace(xl - padding, xl + padding, slice_size)
    yy = np.linspace(yl - padding, yl + padding, slice_size)

    return xx, yy, np.array([point[2]])

@nb.njit(fastmath=True)
def track_point(start, direction, z):
    l = (z - start[2]) / direction[2]
    xl = start[0] + l * direction[0]
    yl = start[1] + l * direction[1]

    return  np.array([xl, yl, z])

@nb.njit(fastmath=True)
def z_interval(start_point, end_point, x_p, y_p, tolerance):
    """Here we calculate the interval in the drift direction for the pixel pID
    using the impact factor"""

    if start_point[0] > end_point[0]:
        start = end_point
        end = start_point
    elif start_point[0] < end_point[0]:
        start = start_point
        end = end_point
    else: # Limit case that we should probably manage better
        return 0, 0

    xs, ys = start[0], start[1]
    xe, ye = end[0], end[1]

    m = (ye - ys) / (xe - xs)
    q = (xe * ys - xs * ye) / (xe - xs)

    a, b, c = m, -1, q

    x_poca = (b*(b*x_p-a*y_p) - a*c)/(a*a+b*b)

    segment = end - start
    length = np.linalg.norm(segment)
    dir3D = segment/length

    if x_poca < start[0]:
        doca = np.sqrt((x_p - start[0])**2 + (y_p - start[1])**2)
        x_poca = start[0]
    elif x_poca > end[0]:
        doca = np.sqrt((x_p - end[0])**2 + (y_p - end[1])**2)
        x_poca = end[0]
    else:
        doca = np.abs(a*x_p+b*y_p+c)/np.sqrt(a*a+b*b)

    plusDeltaZ, minusDeltaZ = 0, 0

    if tolerance > doca:
        length2D = np.sqrt((xe-xs)**2 + (ye-ys)**2)
        dir2D = (end[0]-start[0])/length2D, (end[1]-start[1])/length2D
        deltaL2D = np.sqrt(tolerance**2 - doca**2) # length along the track in 2D

        x_plusDeltaL = x_poca + deltaL2D*dir2D[0] # x coordinates of the tolerance range
        x_minusDeltaL = x_poca - deltaL2D*dir2D[0]
        plusDeltaL = (x_plusDeltaL - start[0])/dir3D[0] # length along the track in 3D
        minusDeltaL = (x_minusDeltaL - start[0])/dir3D[0] # of the tolerance range

        plusDeltaZ = start[2] + dir3D[2] * plusDeltaL # z coordinates of the
        minusDeltaZ = start[2] + dir3D[2] * minusDeltaL # tolerance range

    return min(minusDeltaZ, plusDeltaZ), max(minusDeltaZ, plusDeltaZ)

@nb.njit(fastmath=True)
def get_pixels(track, cols, pixel_size):
    s = (track[cols["x_start"]], track[cols["y_start"]])
    e = (track[cols["x_end"]], track[cols["y_end"]])
    
    start_pixel = (int(round((s[0]- tpc_borders[0][0]) // pixel_size[0])),
                   int(round((s[1]- tpc_borders[1][0]) // pixel_size[1])))

    end_pixel = (int(round((e[0]- tpc_borders[0][0]) // pixel_size[0])),
                 int(round((e[1]- tpc_borders[1][0]) // pixel_size[1])))

    active_pixels = line2pixel_bresenham(start_pixel[0], start_pixel[1],
                                      end_pixel[0], end_pixel[1])

    involved_pixels = []

    for x, y in active_pixels:
        neighbors = ((x, y),
                     (x, y + 1), (x + 1, y),
                     (x, y - 1), (x - 1, y),
                     (x + 1, y + 1), (x - 1, y - 1),
                     (x + 1, y - 1), (x - 1, y + 1))
        nneighbors = ((x + 2, y), (x + 2, y + 1), (x + 2, y + 2), (x + 2, y - 1), (x + 2, y - 2),
                      (x - 2, y), (x - 2, y + 1), (x - 2, y + 2), (x - 2, y - 1), (x + 2, y - 2),
                      (x, y + 2), (x - 1, y + 2), (x + 1, y + 2),
                      (x, y - 2), (x - 1, y - 2), (x + 1, y - 2))

        for ne in (neighbors+nneighbors):
            if ne not in involved_pixels:
                involved_pixels.append(ne)

    return involved_pixels

@nb.njit(fastmath=True)
def line2pixel_bresenham(x0, y0, x1, y1):

    dx = x1 - x0
    dy = y1 - y0

    xsign = 1 if dx > 0 else -1
    ysign = 1 if dy > 0 else -1

    dx = abs(dx)
    dy = abs(dy)

    if dx > dy:
        xx, xy, yx, yy = xsign, 0, 0, ysign
    else:
        dx, dy = dy, dx
        xx, xy, yx, yy = 0, ysign, xsign, 0

    D = 2*dy - dx
    y = 0
    
    pixel = nb.typed.List()
    for x in range(dx + 1):
        pixel.append([x0 + x*xx + y*yx, y0 + x*xy + y*yy])
        if D >= 0:
            y += 1
            D -= 2*dx
        D += 2*dy
        
    return pixel

@nb.njit(fastmath=True)
def pixelID_track(tracks, cols, pixel_size):
    PixelTrackID = nb.typed.List()

    for itrk in range(len(tracks)):
        PixelTrackID.append(get_pixels(tracks[itrk], cols, pixel_size))

    return PixelTrackID


@nb.njit(fastmath=True)
def list2array(pixelTrackIDs, dtype=np.int64):
    lens = [len(pIDs) for pIDs in pixelTrackIDs]
    pIDs_array = np.full((len(pixelTrackIDs), max(lens), 2), np.inf, dtype=dtype)
    for i, pIDs in enumerate(pixelTrackIDs):
        for j, pID in enumerate(pIDs):
            pIDs_array[i][j] = pID
    
    return pIDs_array

@nb.njit(fastmath=True)
def slice_signal(x_p, y_p, weights, xv, yv, this_current_response):
    distances = np.empty(len(xv)*len(yv))
    i = 0
    for x in xv:
        for y in yv:
            distances[i] = exp(-1e2*sqrt((x - x_p)*(x - x_p) + (y - y_p)*(y - y_p)))
            i += 1

    weights_attenuated = weights * distances
    signals = np.outer(weights_attenuated, this_current_response)

    return signals

@nb.njit(fastmath=True)
def _b(x, y, z, start, sigmas, segment, Deltar):
    return -((x-start[0]) / (sigmas[0]*sigmas[0]) * (segment[0]/Deltar) + \
             (y-start[1]) / (sigmas[1]*sigmas[1]) * (segment[1]/Deltar) + \
             (z-start[2]) / (sigmas[2]*sigmas[2]) * (segment[2]/Deltar))

@nb.njit(fastmath=True)
def rho(x, y, z, a, start, sigmas, segment, Deltar, factor):
    """Charge distribution in space"""
    b = _b(x, y, z, start, sigmas, segment, Deltar)
    sqrt_a_2 = 2*sqrt(a)

    delta = (x-start[0])*(x-start[0])/(2*sigmas[0]*sigmas[0]) + \
            (y-start[1])*(y-start[1])/(2*sigmas[1]*sigmas[1]) + \
            (z-start[2])*(z-start[2])/(2*sigmas[2]*sigmas[2])

    expo = exp(b*b/(4*a) - delta)

    integral = sqrt(pi) * \
               (-erf(b/sqrt_a_2) + erf((b + 2*a*Deltar)/sqrt_a_2)) / \
               sqrt_a_2

    return expo * factor * integral

@nb.njit(fastmath=True)
def diffusion_weights(n_electrons, point, start, end, sigmas, slice_size):
    """The function calculates the weights of the charge cloud slice at a
    specified point

    Args:
        n_electrons (int): number of electrons ionized by the track
        point (:obj:`numpy.array`): coordinates of the specified point
        start (:obj:`numpy.array`): coordinates of the track segment start point
        end (:obj:`numpy.array`): coordinates of the track segment end point
        sigmas (:obj:`numpy.array`): diffusion values along the x,y,z axes
        slice_size (int): number of sampling points for the slice

    Returns:
        :obj:`numpy.array`: array containing the weights
    """

    segment = end - start

    Deltar = np.linalg.norm(segment)
    factor = n_electrons/Deltar/(sigmas.prod()*sqrt(8*pi*pi*pi))
    a = ((segment/Deltar)**2 / (2*sigmas**2)).sum()

    xx = np.linspace(point[0] - sigmas[0] * 5,
                     point[0] + sigmas[0] * 5,
                     slice_size)
    yy = np.linspace(point[1] - sigmas[1] * 5,
                     point[1] + sigmas[1] * 5,
                     slice_size)
    zz = np.array([point[2]])

    weights = np.empty(len(xx)*len(yy)*len(zz))
    i = 0
    for x in xx:
        for y in yy:
            for z in zz:
                weights[i] = rho(x, y, z, a, start, sigmas, segment, Deltar, factor)
                i += 1

    return weights * (xx[1]-xx[0]) * (yy[1]-yy[0])


def partial(func, *args):
    @nb.njit
    def inner(*iargs):
        return func(*args, *iargs)
    return inner


@nb.njit(fastmath=True, parallel=True)
def track_current(track, pixels, cols, slice_size, t_sampling, active_pixels, pixel_size, time_padding=20):
    """The function calculates the current induced on each pixel for the selected track

    Args:
        track (:obj:`numpy.array`): array containing track segment information
        pixels (:obj:`numpy.array`): array containing the IDs of the involved pixels
        cols (:obj:`numba.typed.Dict`): Numba dictionary containing columns names for the track array
        slice_size (int): number of points for the sampling of the diffused charge cloud slice
        t_sampling (float): time sampling
        active_pixels (:obj:`numba.typed.Dict`): Numba dictionary where we store the pixel signals
        pixel_size (tuple): size of each pixel on the x and y axes
        time_padding (float, optional): time padding on each side of the induced signal array

    """

    start = np.array([track[cols["x_start"]], track[cols["y_start"]], track[cols["z_start"]]])
    end = np.array([track[cols["x_end"]], track[cols["y_end"]], track[cols["z_end"]]])
    mid_point = (start+end)/2

    segment = end - start
    length = np.linalg.norm(segment)
    direction = segment/length

    sigmas = np.array([track[cols["tranDiff"]],
                       track[cols["tranDiff"]],
                       track[cols["longDiff"]]])
    endcap_size = 5 * track[cols["longDiff"]]

    # Here we calculate the diffusion weights at the center of the track segment
    weights_bulk = diffusion_weights(track[cols["NElectrons"]], mid_point, start, end, sigmas, slice_size)

    # Here we calculate the start and end time of our signal (+- a specified padding)
    # and we round it to our time sampling
    t_start = (track[cols["t_start"]] - time_padding) // t_sampling * t_sampling
    t_end = (track[cols["t_end"]] + time_padding) // t_sampling * t_sampling
    t_length = t_end - t_start
    time_interval = np.linspace(t_start, t_end, int(round(t_length / t_sampling)))

    z_sampling = t_sampling * vdrift

    # The first loop is over the involved pixels
    for i in nb.prange(pixels.shape[0]):
        pID = pixels[i]
        if pID[0] == np.inf:
            break

        x_p = pID[0] * pixel_size[0] + tpc_borders[0][0] + pixel_size[0] / 2
        y_p = pID[1] * pixel_size[1] + tpc_borders[1][0] + pixel_size[1] / 2

        # This is the interval along the drift direction that we are considering
        # We are taking slice of the charge cloud that are within the impact factor
        impact_factor = 3 * np.sqrt(pixel_size[0]**2 + pixel_size[1]**2)
        z_start, z_end = z_interval(start, end, x_p, y_p, impact_factor)
        z_range = np.linspace(z_start, z_end, ceil(abs(z_end-z_start)/z_sampling)+1)

        if z_range.size <= 1:
            continue

        signal = np.zeros_like(time_interval)

        # The second loop is over the slices along the drift direction
        for j in nb.prange(z_range.shape[0]):
            z = z_range[j]
            point = track_point(start, direction, z)
            xv, yv, zv = slice_coordinates(point, track[cols["tranDiff"]] * 5, slice_size)

            # If the slice is near the endcap we calculate the weights again and we don't
            # use the weights calculated at midpoint
            if track[cols["z_end"]] - endcap_size <= z <= track[cols["z_end"]] + endcap_size or \
               track[cols["z_start"]] - endcap_size <= z <= track[cols["z_start"]] + endcap_size:
                weights = diffusion_weights(track[cols["NElectrons"]], point, start, end, sigmas, slice_size)
            else:
                weights = weights_bulk

            # This is the induced current for this z coordinate
            t0 = (z - tpc_borders[2][0]) / vdrift
            current_response_z = current_response(time_interval, t0=t0)

            # Here we multiply the signal for each sampled point in our slice
            # The total signal will be the sum of the signal for each point
            signals = slice_signal(x_p, y_p, weights, xv, yv, current_response_z)
            signal += np.sum(signals, axis=0) * (z_range[1]-z_range[0])

        if not signal.any():
            continue

        # If the pixel is already in the dictionary of the active pixels
        # we add the new signal to the list with its start and end times,
        # otherwise we create a list filled with the signal we just calculated
        t = (pID[0], pID[1])
        if t in active_pixels:
            active_pixels[t].append((t_start, t_end, signal))
        else:
            pixel_signal = nb.typed.List()
            pixel_signal.append((t_start, t_end, signal))
            active_pixels[t] = pixel_signal

@nb.jit
def pixel_response(pixel_signals, anode_t):
    current = np.zeros_like(anode_t)

    for signal in pixel_signals:
        current[(anode_t >= signal[0]) & (anode_t <= signal[1])] += signal[2]

    return current


float_array = nb.types.float64[::1]
pixelID_type = nb.types.Tuple((nb.int64, nb.int64))
signal_type = nb.types.ListType(nb.types.Tuple((nb.float64, nb.float64, float_array)))

@nb.njit(fastmath=True)
def tracks_current(tracks, pIDs_array, cols, pixel_size, t_sampling=1, slice_size=20):
    """This function calculate the current induced on the pixels by the input track segments

    Args:
        track (:obj:`numpy.array`): array containing the tracks segment information
        pIDs_array (:obj:`numpy.array`): array containing the involved pixels for each track
        cols (:obj:`numba.typed.Dict`): Numba dictionary containing columns names for the track array
        pixel_size (tuple): size of each pixel on the x and y axes
        t_sampling (float, optional): time sampling
        slice_size (int, optional): number of points for the sampling of the diffused charge cloud slice

    Return:
        :obj:`numba.typed.Dict`: Numba dictionary containing a list of the signals for each pixel
    """

    active_pixels = nb.typed.Dict.empty(key_type=pixelID_type,
                                        value_type=signal_type)

    for i in nb.prange(tracks.shape[0]):
        track = tracks[i]
        pID = pIDs_array[i]
        track_current(track, pID, cols, slice_size, t_sampling, active_pixels, pixel_size)

    return active_pixels

# @nb.jit
def pixel_from_coordinates(x, y, n_pixels):
    """This function returns the ID of the pixel that covers the specified point

    Args:
        x (float): x coordinate
        y (float): y coordinate
        n_pixels (int): number of pixels for each axis

    Returns:
        tuple: the pixel ID
    """

    x_pixel = np.linspace(tpc_borders[0][0], tpc_borders[0][1], n_pixels)
    y_pixel = np.linspace(tpc_borders[1][0], tpc_borders[1][1], n_pixels)
    return np.digitize(x, x_pixel), np.digitize(y, y_pixel)
