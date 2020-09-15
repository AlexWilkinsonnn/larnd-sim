"""
Module that calculates the current induced by edep-sim track segments
on the pixels
"""

from math import pi, ceil, sqrt, erf, exp, cos, sin

import numba as nb
import numpy as np

from numba import cuda
from .consts import tpc_borders, vdrift, pixel_size, time_padding
from .consts import t_sampling, sampled_points
from . import indeces as i

import logging
logging.basicConfig()
logger = logging.getLogger('detsim')
logger.setLevel(logging.INFO)
logger.info("DETSIM MODULE PARAMETERS")
logger.info("""TPC parameters
Drift velocity: %g us/cm
Time sampling: %g us
Time padding: %g us
TPC borders: (%g cm, %g cm) x, (%g cm, %g cm) y, (%g cm, %g cm) z
Sampled points per slice: %g""" % (vdrift, t_sampling, time_padding, *tpc_borders[0], *tpc_borders[1], *tpc_borders[2], sampled_points))

logger.info("""Pixel parameters
Pixel size: (%g x %g) cm^2
""" % (pixel_size[0], pixel_size[1]))

@cuda.jit
def time_intervals(track_starts, time_max, tracks):
    """
    Find the value of the longest signal time and stores the start
    time of each segment.

    Args:
        track_starts (:obj:`numpy.ndarray`): array where
            we store the segments start time
        time_max (:obj:`numpy.ndarray`): array where we store
            the longest signal time
        tracks (:obj:`numpy.ndarray`): array containing the segment
            information
    """
    itrk = cuda.grid(1)

    if itrk < tracks.shape[0]:
        track = tracks[itrk]
        t_end = (track[i.t_end] + time_padding) // t_sampling * t_sampling
        t_start = (track[i.t_start] - time_padding) // t_sampling * t_sampling
        t_length = t_end - t_start
        track_starts[itrk] = t_start
        cuda.atomic.max(time_max, 0, int(round(t_length / t_sampling))+1)

@cuda.jit(device=True)
def z_interval(start_point, end_point, x_p, y_p, tolerance):
    """
    Here we calculate the interval in the drift direction for the pixel pID
    using the impact factor

    Args:
        start_point (tuple): coordinates of the segment start
        end_point (tuple): coordinates of the segment end
        x_p (float): pixel center `x` coordinate
        y_p (float): pixel center `y` coordinate
        tolerance (float): maximum distance between the pixel center and
            the segment

    Returns:
        tuple: `z` coordinate of the point of closest approach (POCA),
        `z` coordinate of the first slice, `z` coordinate of the last slice.
        (0,0,0) if POCA > tolerance.
    """
    if start_point[0] > end_point[0]:
        start = end_point
        end = start_point
    elif start_point[0] < end_point[0]:
        start = start_point
        end = end_point
    else: # Limit case that we should probably manage better
        return 0, 0, 0

    xs, ys = start[0], start[1]
    xe, ye = end[0], end[1]

    m = (ye - ys) / (xe - xs)
    q = (xe * ys - xs * ye) / (xe - xs)

    a, b, c = m, -1, q

    x_poca = (b*(b*x_p-a*y_p) - a*c)/(a*a+b*b)

    length = sqrt((end[0]-start[0])**2+(end[1]-start[1])**2+(end[2]-start[2])**2)
    dir3D = (end[0] - start[0])/length, (end[1] - start[1])/length, (end[2] - start[2])/length


    if x_poca < start[0]:
        doca = sqrt((x_p - start[0])**2 + (y_p - start[1])**2)
        x_poca = start[0]
    elif x_poca > end[0]:
        doca = sqrt((x_p - end[0])**2 + (y_p - end[1])**2)
        x_poca = end[0]
    else:
        doca = abs(a*x_p+b*y_p+c)/sqrt(a*a+b*b)

    z_poca = start[2] + (x_poca - start[0])/dir3D[0]*dir3D[2]

    plusDeltaZ, minusDeltaZ = 0, 0

    if tolerance > doca:
        length2D = sqrt((xe-xs)**2 + (ye-ys)**2)
        dir2D = (end[0]-start[0])/length2D, (end[1]-start[1])/length2D
        deltaL2D = sqrt(tolerance**2 - doca**2) # length along the track in 2D

        x_plusDeltaL = x_poca + deltaL2D*dir2D[0] # x coordinates of the tolerance range
        x_minusDeltaL = x_poca - deltaL2D*dir2D[0]
        plusDeltaL = (x_plusDeltaL - start[0])/dir3D[0] # length along the track in 3D
        minusDeltaL = (x_minusDeltaL - start[0])/dir3D[0] # of the tolerance range

        plusDeltaZ = start[2] + dir3D[2] * plusDeltaL # z coordinates of the
        minusDeltaZ = start[2] + dir3D[2] * minusDeltaL # tolerance range

        return z_poca, min(minusDeltaZ, plusDeltaZ), max(minusDeltaZ, plusDeltaZ)
    else:
        return 0, 0, 0

@cuda.jit(device=True)
def _b(x, y, z, start, sigmas, segment, Deltar):
    return -((x-start[0]) / (sigmas[0]*sigmas[0]) * (segment[0]/Deltar) + \
             (y-start[1]) / (sigmas[1]*sigmas[1]) * (segment[1]/Deltar) + \
             (z-start[2]) / (sigmas[2]*sigmas[2]) * (segment[2]/Deltar))

@cuda.jit(device=True)
def rho(point, q, start, sigmas, segment):
    """
    Function that returns the amount of charge at a certain point in space

    Args:
        point (tuple): point coordinates
        q (float): total charge
        start (tuple): segment start coordinates
        sigmas (tuple): diffusion coefficients
        segment (tuple): segment sizes

    Returns:
        float: the amount of charge at `point`.
    """
    x, y, z = point
    Deltax, Deltay, Deltaz = segment[0], segment[1], segment[2]
    Deltar = sqrt(Deltax**2+Deltay**2+Deltaz**2)
    a = ((Deltax/Deltar) * (Deltax/Deltar) / (2*sigmas[0]*sigmas[0]) + \
         (Deltay/Deltar) * (Deltay/Deltar) / (2*sigmas[1]*sigmas[1]) + \
         (Deltaz/Deltar) * (Deltaz/Deltar) / (2*sigmas[2]*sigmas[2]))
    factor = q/Deltar/(sigmas[0]*sigmas[1]*sigmas[2]*sqrt(8*pi*pi*pi))
    sqrt_a_2 = 2*sqrt(a)

    b = _b(x, y, z, start, sigmas, segment, Deltar)

    delta = (x-start[0])*(x-start[0])/(2*sigmas[0]*sigmas[0]) + \
            (y-start[1])*(y-start[1])/(2*sigmas[1]*sigmas[1]) + \
            (z-start[2])*(z-start[2])/(2*sigmas[2]*sigmas[2])

    expo = exp(b*b/(4*a) - delta)

    integral = sqrt(pi) * \
               (-erf(b/sqrt_a_2) + erf((b + 2*a*Deltar)/sqrt_a_2)) / \
               sqrt_a_2

    return expo * integral * factor


@cuda.jit(device=True)
def current_model(t, t0, x, y):
    """
    Parametrization of the induced current on the pixel, which depends
    on the of arrival at the anode (:math:`t_0`) and on the position
    on the pixel pad.

    Args:
        t (float): time where we evaluate the current
        t0 (float): time of arrival at the anode
        x (float): distance between the point on the pixel and the pixel center
            on the :math:`x` axis
        y (float): distance between the point on the pixel and the pixel center
            on the :math:`y` axis

    Returns:
        float: the induced current at time :math:`t`
    """
    b_params = (40.74727999, -288.41137404, -288.41137404, 1247.51482664, 517.99360585, 517.993605)
    c_params = (2.504075, -4.98133949, -4.98133949, -5.01073463, 81.93629314, 81.93629314)
    d_params = (0.68403238, -1.11708586, -1.11708586, 9.08695733, -5.55778424, -5.55778424)
    t0_params = (2.94805382, -2.70495514, -2.70495514, 4.82499082, 20.81401515, 20.81401515)

    b = b_params[0] + b_params[1]*x + b_params[2]*y \
        + b_params[3]*x*y + b_params[4]*x*x + b_params[5]*y*y
    c = c_params[0] + c_params[1]*x + c_params[2]*y \
        + c_params[3]*x*y + c_params[4]*x*x + c_params[5]*y*y
    d = d_params[0] + d_params[1]*x + d_params[2]*y \
        + d_params[3]*x*y + d_params[4]*x*x + d_params[5]*y*y
    t0 += t0_params[0] + t0_params[1]*x + t0_params[2]*y \
          + t0_params[3]*x*y + t0_params[4]*x*x + t0_params[5]*y*y

    c *= 1e-19
    a = (1.603e-19-c*d)/b

    if t0-t > 0:
        return a * exp((t-t0)/b) + c * exp((t-t0)/d)
    else:
        return 0


@cuda.jit(device=True)
def current_signal(pixel_point, point, q, start, sigmas, segment, time, t0):
    """
    This function calculates current induces on a pixel by a segment, which depends
    on the distance between the segment projection on the anode and the pixel center.

    Args:
        pixel_point (tuple): pixel coordinates
        point (tuple): coordinates of the segment in the slice
        q (float): total track charge
        start (tuple): segment start coordinates
        sigmas (tuple): diffusion coefficients in the spatial dimensions?
        segment (tuple): segment sizes in the spatial dimensions
        time (float): time when we evault the induced current
        t0 (float): time when the segment reaches the anode

    Returns:
        float: the induced current on the pixel at time :math:`t`.
    """
    total_signal = 0
    r_step = sigmas[0] * 3 / (sampled_points - 1)
    theta_step = 2 * pi / (sampled_points*2 - 1)
    # we sample the slice in polar coordinates
    for ir in range(sampled_points):
        for itheta in range(sampled_points*2):
            r = ir*r_step
            theta = itheta*theta_step
            xv = point[0] + r*cos(theta)
            yv = point[1] + r*sin(theta)
            x_dist = abs(pixel_point[0] - xv)
            y_dist = abs(pixel_point[1] - yv)
            if x_dist < pixel_size[0]/2. and y_dist < pixel_size[1]/2.:
                charge = rho((xv, yv, point[2]), q, start, sigmas, segment)
                total_signal += charge * current_model(time, t0, x_dist, y_dist) \
                                * 1./2. * theta_step * r_step**2 *((ir+1)**2 - ir**2)
                                # this is the circle sector area

    return total_signal

@cuda.jit(device=True)
def track_point(start, direction, z):
    """
    This function returns the segment coordinates for a point along the `z` coordinate

    Args:
        start (tuple): start coordinates
        direction (tuple): direction coordinates
        z (float): `z` coordinate corresponding to the `x`, `y` coordinates

    Returns:
        tuple: the (x,y) pair of coordinates for the segment at `z`
    """
    l = (z - start[2]) / direction[2]
    xl = start[0] + l * direction[0]
    yl = start[1] + l * direction[1]

    return xl, yl

@cuda.jit
def tracks_current(signals, pixels, tracks):
    """
    This CUDA kernel calculates the charge induced on the pixels by the input tracks.

    Args:
        signals (:obj:`numpy.array`): empty 3D array with dimensions S x P x T,
            where S is the number of track segments, P is the number of pixels, and T is
            the number of time ticks. The output is stored here.
        pixels (:obj:`numpy.array`): 3D array with dimensions S x P x 2, where S is
            the number of track segments, P is the number of pixels and the third dimension
            contains the two pixel ID numbers.
    """
    itrk, ipix, it = cuda.grid(3)

    if itrk < signals.shape[0] and ipix < signals.shape[1] and it < signals.shape[2]:
        t = tracks[itrk]
        pID = pixels[itrk][ipix]

        if pID[0] >= 0 and pID[1] >= 0:

            # Pixel coordinates
            x_p = pID[0] * pixel_size[0] + tpc_borders[0][0] + pixel_size[0] / 2
            y_p = pID[1] * pixel_size[1] + tpc_borders[1][0] + pixel_size[1] / 2
            this_pixel_point = (x_p, y_p)

            impact_factor = 3 * sqrt(pixel_size[0]**2 + pixel_size[1]**2)

            start = (t[i.x_start], t[i.y_start], t[i.z_start])
            end = (t[i.x_end], t[i.y_end], t[i.z_end])
            segment = (end[0]-start[0], end[1]-start[1], end[2]-start[2])
            length = sqrt(segment[0]**2 + segment[1]**2 + segment[2]**2)
            direction = (segment[0]/length, segment[1]/length, segment[2]/length)

            sigmas = (t[i.tran_diff], t[i.tran_diff], t[i.long_diff])

            z_sampling = t_sampling * vdrift
            z_poca, z_start, z_end = z_interval(start, end, x_p, y_p, impact_factor)

            if z_start != 0 and z_end != 0:
                z_range_up = ceil(abs(z_end-z_poca)/z_sampling)
                z_range_down = ceil(abs(z_poca-z_start)/z_sampling)
                z_step = (z_end-z_poca)/(z_range_up)

                t_start = (t[i.t_start] - time_padding) // t_sampling * t_sampling
                # Loop over the slices along the z direction
                for iz in range(-z_range_down, z_range_up+1):
                    z_t = z_poca + iz*z_step
                    t0 = (z_t - tpc_borders[2][0]) / vdrift
                    x_t, y_t = track_point(start, direction, z_t)
                    time_tick = t_start + it*t_sampling

                    if time_tick < t0+5:
                        signals[itrk][ipix][it] += current_signal(this_pixel_point,
                                                                  (x_t, y_t, z_t),
                                                                  t[i.n_electrons],
                                                                  start, sigmas, segment,
                                                                  time_tick, t0) * z_step

@nb.jit(forceobj=True)
def pixel_from_coordinates(x, y, n_pixels):
    """
    This function returns the ID of the pixel that covers the specified point

    Args:
        x (float): x coordinate
        y (float): y coordinate
        n_pixels (int): number of pixels for each axis

    Returns:
        tuple: the pixel ID
    """

    x_pixel = np.linspace(tpc_borders[0][0], tpc_borders[0][1], n_pixels[0])
    y_pixel = np.linspace(tpc_borders[1][0], tpc_borders[1][1], n_pixels[1])
    return np.digitize(x, x_pixel), np.digitize(y, y_pixel)

@cuda.jit
def sum_pixel_signals(pixels_signals, signals, track_starts, index_map):
    """
    This function sums the induced current signals on the same pixel.
    
    Args:
        pixels_signals (:obj:`numpy.array`): 2D array that will contain the
            summed signal for each pixel. First dimension is the pixel ID, second
            dimension is the time tick
        signals (:obj:`numpy.array`): 3D array with dimensions S x P x T,
            where S is the number of track segments, P is the number of pixels, and T is
            the number of time ticks.
        track_starts (:obj:`numpy.array`): 1D array containing the starting time of 
            each track
        index_map (:obj:`numpy.array`): 2D array containing the correspondence between
            the track index and the pixel ID index.
    """
    it, ipix, itick = cuda.grid(3)
    
    if it < signals.shape[0] and ipix < signals.shape[1]:
        index = index_map[it][ipix]
        start_tick = track_starts[it] // t_sampling
        if itick < signals.shape[2] and index >= 0:
            itime = int(start_tick+itick)
            cuda.atomic.add(pixels_signals, (index, itime), signals[it][ipix][itick])