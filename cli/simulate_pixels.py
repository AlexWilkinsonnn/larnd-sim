#!/usr/bin/env python
"""
Command-line interface to larnd-sim module.
"""
from math import ceil
from time import time
import warnings

import numpy as np
import numpy.lib.recfunctions as rfn

import cupy as cp
from cupy.cuda.nvtx import RangePush, RangePop

import fire
import h5py

from numba.cuda import device_array
from numba.cuda.random import create_xoroshiro128p_states
from numba.core.errors import NumbaPerformanceWarning

from tqdm import tqdm

from larndsim import consts
from larndsim.cuda_dict import CudaDict

SEED = int(time())
BATCH_SIZE = 4000
EVENT_BATCH_SIZE = 10

LOGO = """
  _                      _            _
 | |                    | |          (_)
 | | __ _ _ __ _ __   __| |______ ___ _ _ __ ___
 | |/ _` | '__| '_ \ / _` |______/ __| | '_ ` _ \\
 | | (_| | |  | | | | (_| |      \__ \ | | | | | |
 |_|\__,_|_|  |_| |_|\__,_|      |___/_|_| |_| |_|

"""

warnings.simplefilter('ignore', category=NumbaPerformanceWarning)

def swap_coordinates(tracks):
    """
    Swap x and z coordinates in tracks.
    This is because the convention in larnd-sim is different
    from the convention in edep-sim. FIXME.

    Args:
        tracks (:obj:`numpy.ndarray`): tracks array.

    Returns:
        :obj:`numpy.ndarray`: tracks with swapped axes.
    """
    x_start = np.copy(tracks['x_start'] )
    x_end = np.copy(tracks['x_end'])
    x = np.copy(tracks['x'])

    tracks['x_start'] = np.copy(tracks['z_start'])
    tracks['x_end'] = np.copy(tracks['z_end'])
    tracks['x'] = np.copy(tracks['z'])

    tracks['z_start'] = x_start
    tracks['z_end'] = x_end
    tracks['z'] = x

    return tracks

def maybe_create_rng_states(n, seed=0, rng_states=None):
    """Create or extend random states for CUDA kernel"""

    if rng_states is None:
        return create_xoroshiro128p_states(n, seed=seed)

    if n > len(rng_states):
        new_states = device_array(n, dtype=rng_states.dtype)
        new_states[:len(rng_states)] = rng_states
        new_states[len(rng_states):] = create_xoroshiro128p_states(n - len(rng_states), seed=seed)
        return new_states

    return rng_states

def run_simulation(input_filename,
                   pixel_layout,
                   detector_properties,
                   output_filename,
                   response_file='../larndsim/bin/response_44.npy',
                   light_lut_filename='../larndsim/bin/lightLUT.npz',
                   light_det_noise_filename='../larndsim/bin/light_noise-module0.npy',
                   bad_channels=None,
                   n_tracks=None,
                   pixel_thresholds_file=None):
    """
    Command-line interface to run the simulation of a pixelated LArTPC

    Args:
        input_filename (str): path of the edep-sim input file
        pixel_layout (str): path of the YAML file containing the pixel
            layout and connection details.
        detector_properties (str): path of the YAML file containing
            the detector properties
        output_filename (str): path of the HDF5 output file. If not specified
            the output is added to the input file.
        response_file (str, optional): path of the Numpy array containing the pre-calculated
            field responses. Defaults to ../larndsim/bin/response_44.npy.
        light_lut_file (str, optional): path of the Numpy array containing the light
            look-up table. Defaults to ../larndsim/bin/lightLUT.npy.
        bad_channels (str, optional): path of the YAML file containing the channels to be
            disabled. Defaults to None
        n_tracks (int, optional): number of tracks to be simulated. Defaults to None
            (all tracks).
        pixel_thresholds_file (str): path to npz file containing pixel thresholds. Defaults
            to None.
    """
    start_simulation = time()

    RangePush("run_simulation")

    print(LOGO)
    print("**************************\nLOADING SETTINGS AND INPUT\n**************************")
    print("Output file:", output_filename)
    print("Random seed:", SEED)
    print("Batch size:", BATCH_SIZE)
    print("Pixel layout file:", pixel_layout)
    print("Detector properties file:", detector_properties)
    print("edep-sim input file:", input_filename)
    print("Response file:", response_file)
    if bad_channels:
        print("Disabled channel list: ", bad_channels)
    RangePush("load_detector_properties")
    consts.load_properties(detector_properties, pixel_layout)
    from larndsim.consts import light, detector, physics
    RangePop()

    RangePush("load_larndsim_modules")
    # Here we load the modules after loading the detector properties
    # maybe can be implemented in a better way?
    from larndsim import (quenching, drifting, detsim, pixels_from_track, fee,
        lightLUT, light_sim)
    RangePop()

    RangePush("load_pixel_thresholds")
    if pixel_thresholds_file is not None:
        print("Pixel thresholds file:", pixel_thresholds_file)
        pixel_thresholds_lut = CudaDict.load(pixel_thresholds_file, 256)
    else:
        pixel_thresholds_lut = CudaDict(cp.array([fee.DISCRIMINATION_THRESHOLD]), 1, 1)
    RangePop()

    RangePush("load_hd5_file")
    # First of all we load the edep-sim output
    with h5py.File(input_filename, 'r') as f:
        tracks = np.array(f['segments'])
        try:
            trajectories = np.array(f['trajectories'])
            input_has_trajectories = True
        except KeyError:
            input_has_trajectories = False

        try:
            vertices = np.array(f['vertices'])
            input_has_vertices = True
        except KeyError:
            print("Input file does not have true vertices info")
            input_has_vertices = False

    RangePop()

    # Makes an empty array to store data from lightlut
    if light.LIGHT_SIMULATED:
        light_sim_dat = np.zeros([len(tracks), light.N_OP_CHANNEL],
                                 dtype=[('n_photons_det','f4'),('t0_det','f4')])
        track_light_voxel = np.zeros([len(tracks), 3], dtype='i4')

    if tracks.size == 0:
        print("Empty input dataset, exiting")
        return

    if n_tracks:
        tracks = tracks[:n_tracks]
        if light.LIGHT_SIMULATED:
            light_sim_dat = light_sim_dat[:n_tracks]

    if 'n_photons' not in tracks.dtype.names:
        n_photons = np.zeros(tracks.shape[0], dtype=[('n_photons', 'f4')])
        tracks = rfn.merge_arrays((tracks, n_photons), flatten=True)


    if 't0' not in tracks.dtype.names:
        # the t0 key refers to the time of energy deposition
        # in the input files, it is called 't'
        # this is only true for older edep inputs (which are included in `examples/`)
        t0 = np.array(tracks['t'].copy(), dtype=[('t0', 'f4')])
        t0_start = np.array(tracks['t_start'].copy(), dtype=[('t0_start', 'f4')])
        t0_end = np.array(tracks['t_end'].copy(), dtype=[('t0_end', 'f4')])
        tracks = rfn.merge_arrays((tracks, t0, t0_start, t0_end), flatten=True)

        # then, re-initialize the t key to zero
        # in larnd-sim, this key is the time at the anode
        tracks['t'] = np.zeros(tracks.shape[0], dtype=[('t', 'f4')])
        tracks['t_start'] = np.zeros(tracks.shape[0], dtype=[('t_start', 'f4')])
        tracks['t_end'] = np.zeros(tracks.shape[0], dtype=[('t_end', 'f4')])

    # Here we swap the x and z coordinates of the tracks
    # because of the different convention in larnd-sim wrt edep-sim
    tracks = swap_coordinates(tracks)

    response = cp.load(response_file)

    TPB = 256
    BPG = ceil(tracks.shape[0] / TPB)

    print("******************\nRUNNING SIMULATION\n******************")
    # We calculate the number of electrons after recombination (quenching module)
    # and the position and number of electrons after drifting (drifting module)
    print("Quenching electrons..." , end="")
    start_quenching = time()
    quenching.quench[BPG,TPB](tracks, physics.BIRKS)
    end_quenching = time()
    print(f" {end_quenching-start_quenching:.2f} s")

    print("Drifting electrons...", end="")
    start_drifting = time()
    drifting.drift[BPG,TPB](tracks)
    end_drifting = time()
    print(f" {end_drifting-start_drifting:.2f} s")

    if light.LIGHT_SIMULATED:
        print("Calculating optical responses...", end="")
        start_light_time = time()
        lut = np.load(light_lut_filename)['arr']
        light_noise = cp.load(light_det_noise_filename)
        TPB = 256
        BPG = ceil(tracks.shape[0] / TPB)
        lightLUT.calculate_light_incidence[BPG,TPB](tracks, lut, light_sim_dat, track_light_voxel)
        print(f" {time()-start_light_time:.2f} s")

    with h5py.File(output_filename, 'a') as output_file:
        output_file.create_dataset("tracks", data=tracks)
        if light.LIGHT_SIMULATED:
            output_file.create_dataset('light_dat', data=light_sim_dat)
        if input_has_trajectories:
            output_file.create_dataset("trajectories", data=trajectories)
        if input_has_vertices:
            output_file.create_dataset("vertices", data=vertices)

    # create a lookup table that maps between unique event ids and the segments in the file
    tot_evids = np.unique(tracks['eventID'])
    _, _, start_idx = np.intersect1d(tot_evids, tracks['eventID'], return_indices=True)
    _, _, rev_idx = np.intersect1d(tot_evids, tracks['eventID'][::-1], return_indices=True)
    end_idx = len(tracks['eventID']) - 1 - rev_idx

    # create a lookup table for event timestamps
    event_times = fee.gen_event_times(tot_evids.max()+1, 0)

    # We divide the sample in portions that can be processed by the GPU
    step = 1

    # charge simulation
    event_id_list = []
    adc_tot_list = []
    adc_tot_ticks_list = []
    track_pixel_map_tot = []
    unique_pix_tot = []
    current_fractions_tot = []

    # light simulation
    light_event_id_list = []
    light_start_time_list = []
    light_trigger_idx_list = []
    light_op_channel_idx_list = []
    light_waveforms_list = []
    light_waveforms_true_track_id_list = []
    light_waveforms_true_photons_list = []

    # pre-allocate some random number states
    rng_states = maybe_create_rng_states(1024*256, seed=0)
    last_time = 0
    for ievd in tqdm(range(0, tot_evids.shape[0], step),
                     desc='Simulating events...', ncols=80, smoothing=0):

        first_event = tot_evids[ievd]
        last_event = tot_evids[min(ievd+step, tot_evids.shape[0]-1)]

        if first_event == last_event:
            last_event += 1

        # load a subset of segments from the file and process those that are from the current event
        track_slice = slice(min(start_idx[ievd:ievd + step]), max(end_idx[ievd:ievd + step])+1)
        track_subset = tracks[track_slice]
        event_mask = (track_subset['eventID'] >= first_event) & (track_subset['eventID'] < last_event)
        evt_tracks = track_subset[event_mask]
        first_trk_id = np.where(track_subset['eventID'] == evt_tracks['eventID'][0])[0][0] + min(start_idx[ievd:ievd + step])

        for itrk in tqdm(range(0, evt_tracks.shape[0], BATCH_SIZE),
                         desc='  Simulating event %i batches...' % ievd, leave=False, ncols=80):
            selected_tracks = evt_tracks[itrk:itrk+BATCH_SIZE]
            RangePush("event_id_map")
            event_ids = selected_tracks['eventID']
            unique_eventIDs = np.unique(event_ids)
            RangePop()

            # We find the pixels intersected by the projection of the tracks on
            # the anode plane using the Bresenham's algorithm. We also take into
            # account the neighboring pixels, due to the transverse diffusion of the charges.
            RangePush("pixels_from_track")
            max_radius = ceil(max(selected_tracks["tran_diff"])*5/detector.PIXEL_PITCH)

            TPB = 128
            BPG = ceil(selected_tracks.shape[0] / TPB)
            max_pixels = np.array([0])
            pixels_from_track.max_pixels[BPG,TPB](selected_tracks, max_pixels)

            # This formula tries to estimate the maximum number of pixels which can have
            # a current induced on them.
            max_neighboring_pixels = (2*max_radius+1)*max_pixels[0]+(1+2*max_radius)*max_radius*2

            active_pixels = cp.full((selected_tracks.shape[0], max_pixels[0]), -1, dtype=np.int32)
            neighboring_pixels = cp.full((selected_tracks.shape[0], max_neighboring_pixels), -1, dtype=np.int32)
            n_pixels_list = cp.zeros(shape=(selected_tracks.shape[0]))

            if not active_pixels.shape[1] or not neighboring_pixels.shape[1]:
                continue

            pixels_from_track.get_pixels[BPG,TPB](selected_tracks,
                                                  active_pixels,
                                                  neighboring_pixels,
                                                  n_pixels_list,
                                                  max_radius)
            RangePop()

            RangePush("unique_pix")
            shapes = neighboring_pixels.shape
            joined = neighboring_pixels.reshape(shapes[0] * shapes[1])
            unique_pix = cp.unique(joined)
            unique_pix = unique_pix[(unique_pix != -1)]
            RangePop()

            if not unique_pix.shape[0]:
                continue

            RangePush("time_intervals")
            # Here we find the longest signal in time and we store an array with the start in time of each track
            max_length = cp.array([0])
            track_starts = cp.empty(selected_tracks.shape[0])
            detsim.time_intervals[BPG,TPB](track_starts, max_length, selected_tracks)
            RangePop()

            RangePush("tracks_current")
            # Here we calculate the induced current on each pixel
            signals = cp.zeros((selected_tracks.shape[0],
                                neighboring_pixels.shape[1],
                                cp.asnumpy(max_length)[0]), dtype=np.float32)
            TPB = (8,8,8)
            BPG_X = ceil(signals.shape[0] / TPB[0])
            BPG_Y = ceil(signals.shape[1] / TPB[1])
            BPG_Z = ceil(signals.shape[2] / TPB[2])
            BPG = (BPG_X, BPG_Y, BPG_Z)
            rng_states = maybe_create_rng_states(int(np.prod(TPB[:2]) * np.prod(BPG[:2])), seed=SEED+ievd+itrk, rng_states=rng_states)
            detsim.tracks_current_mc[BPG,TPB](signals, neighboring_pixels, selected_tracks, response, rng_states)
            RangePop()

            RangePush("pixel_index_map")
            # Here we create a map between tracks and index in the unique pixel array
            pixel_index_map = cp.full((selected_tracks.shape[0], neighboring_pixels.shape[1]), -1)
            for i_ in range(selected_tracks.shape[0]):
                compare = neighboring_pixels[i_, ..., cp.newaxis] == unique_pix
                indices = cp.where(compare)
                pixel_index_map[i_, indices[0]] = indices[1]
            RangePop()

            RangePush("track_pixel_map")
            # Mapping between unique pixel array and track array index
            track_pixel_map = cp.full((unique_pix.shape[0], detsim.MAX_TRACKS_PER_PIXEL), -1)
            TPB = 32
            BPG = ceil(unique_pix.shape[0] / TPB)
            detsim.get_track_pixel_map[BPG, TPB](track_pixel_map, unique_pix, neighboring_pixels)
            RangePop()

            RangePush("sum_pixels_signals")
            # Here we combine the induced current on the same pixels by different tracks
            TPB = (1,1,64)
            BPG_X = ceil(signals.shape[0] / TPB[0])
            BPG_Y = ceil(signals.shape[1] / TPB[1])
            BPG_Z = ceil(signals.shape[2] / TPB[2])
            BPG = (BPG_X, BPG_Y, BPG_Z)
            pixels_signals = cp.zeros((len(unique_pix), len(detector.TIME_TICKS)))
            pixels_tracks_signals = cp.zeros((len(unique_pix),
                                              len(detector.TIME_TICKS),
                                              track_pixel_map.shape[1]))
            detsim.sum_pixel_signals[BPG,TPB](pixels_signals,
                                              signals,
                                              track_starts,
                                              pixel_index_map,
                                              track_pixel_map,
                                              pixels_tracks_signals)
            RangePop()

            RangePush("get_adc_values")
            # Here we simulate the electronics response (the self-triggering cycle) and the signal digitization
            time_ticks = cp.linspace(0, len(unique_eventIDs) * detector.TIME_INTERVAL[1], pixels_signals.shape[1]+1)
            integral_list = cp.zeros((pixels_signals.shape[0], fee.MAX_ADC_VALUES))
            adc_ticks_list = cp.zeros((pixels_signals.shape[0], fee.MAX_ADC_VALUES))
            current_fractions = cp.zeros((pixels_signals.shape[0], fee.MAX_ADC_VALUES, track_pixel_map.shape[1]))

            TPB = 128
            BPG = ceil(pixels_signals.shape[0] / TPB)
            rng_states = maybe_create_rng_states(int(TPB * BPG), seed=SEED+ievd+itrk, rng_states=rng_states)
            pixel_thresholds_lut.tpb = TPB
            pixel_thresholds_lut.bpg = BPG
            pixel_thresholds = pixel_thresholds_lut[unique_pix.ravel()].reshape(unique_pix.shape)

            fee.get_adc_values[BPG, TPB](pixels_signals,
                                         pixels_tracks_signals,
                                         time_ticks,
                                         integral_list,
                                         adc_ticks_list,
                                         0,
                                         rng_states,
                                         current_fractions,
                                         pixel_thresholds)

            adc_list = fee.digitize(integral_list)
            adc_event_ids = np.full(adc_list.shape, unique_eventIDs[0]) # FIXME: only works if looping on a single event
            RangePop()

            event_id_list.append(adc_event_ids)
            adc_tot_list.append(adc_list)
            adc_tot_ticks_list.append(adc_ticks_list)
            unique_pix_tot.append(unique_pix)
            current_fractions_tot.append(current_fractions)
            track_pixel_map[track_pixel_map != -1] += first_trk_id + itrk
            track_pixel_map_tot.append(track_pixel_map)

            # ~~~ Light detector response simulation ~~~
            if light.LIGHT_SIMULATED:
                RangePush("sum_light_signals")
                light_inc = light_sim_dat[track_slice][event_mask][itrk:itrk+BATCH_SIZE]
                selected_track_id = cp.arange(track_slice.start, track_slice.stop)[event_mask][itrk:itrk+BATCH_SIZE]
                n_light_ticks, light_t_start = light_sim.get_nticks(light_inc)

                n_light_det = light_inc.shape[-1]
                light_sample_inc = cp.zeros((n_light_det,n_light_ticks), dtype='f4')
                light_sample_inc_true_track_id = cp.full((n_light_det, n_light_ticks, light.MAX_MC_TRUTH_IDS), -1, dtype='i8')
                light_sample_inc_true_photons = cp.zeros((n_light_det, n_light_ticks, light.MAX_MC_TRUTH_IDS), dtype='f8')

                TPB = (1,64)
                BPG = (ceil(light_sample_inc.shape[0] / TPB[0]),
                       ceil(light_sample_inc.shape[1] / TPB[1]))
                light_sim.sum_light_signals[BPG, TPB](
                    selected_tracks, track_light_voxel[track_slice][event_mask][itrk:itrk+BATCH_SIZE], selected_track_id,
                    light_inc, lut, light_t_start, light_sample_inc, light_sample_inc_true_track_id,
                    light_sample_inc_true_photons)
                RangePop()
                if light_sample_inc_true_track_id.shape[-1] > 0 and cp.any(light_sample_inc_true_track_id[...,-1] != -1):
                    warnings.warn(f"Maximum number of true segments ({light.MAX_MC_TRUTH_IDS}) reached in backtracking info, consider increasing MAX_MC_TRUTH_IDS (larndsim/consts/light.py)")

                RangePush("sim_scintillation")
                light_sample_inc_scint = cp.zeros_like(light_sample_inc)
                light_sample_inc_scint_true_track_id = cp.full_like(light_sample_inc_true_track_id, -1)
                light_sample_inc_scint_true_photons = cp.zeros_like(light_sample_inc_true_photons)
                light_sim.calc_scintillation_effect[BPG, TPB](
                    light_sample_inc, light_sample_inc_true_track_id, light_sample_inc_true_photons, light_sample_inc_scint,
                    light_sample_inc_scint_true_track_id, light_sample_inc_scint_true_photons)

                light_sample_inc_disc = cp.zeros_like(light_sample_inc)
                rng_states = maybe_create_rng_states(int(np.prod(TPB) * np.prod(BPG)),
                                                     seed=SEED+ievd+itrk, rng_states=rng_states)
                light_sim.calc_stat_fluctuations[BPG, TPB](light_sample_inc_scint, light_sample_inc_disc, rng_states)
                RangePop()

                RangePush("sim_light_det_response")
                light_response = cp.zeros_like(light_sample_inc)
                light_response_true_track_id = cp.full_like(light_sample_inc_true_track_id, -1)
                light_response_true_photons = cp.zeros_like(light_sample_inc_true_photons)
                light_sim.calc_light_detector_response[BPG, TPB](
                    light_sample_inc_disc, light_sample_inc_scint_true_track_id, light_sample_inc_scint_true_photons,
                    light_response, light_response_true_track_id, light_response_true_photons)
                light_response += cp.array(light_sim.gen_light_detector_noise(light_response.shape, light_noise))
                RangePop()

                RangePush("sim_light_triggers")
                trigger_idx, op_channel_idx = light_sim.get_triggers(light_response)
                digit_samples = ceil((light.LIGHT_TRIG_WINDOW[1] + light.LIGHT_TRIG_WINDOW[0]) / light.LIGHT_DIGIT_SAMPLE_SPACING)
                TPB = (1,1,64)
                BPG = (ceil(trigger_idx.shape[0] / TPB[0]),
                       ceil(op_channel_idx.shape[1] / TPB[1]),
                       ceil(digit_samples / TPB[2]))

                light_digit_signal, light_digit_signal_true_track_id, light_digit_signal_true_photons = light_sim.sim_triggers(
                    BPG, TPB, light_response, light_response_true_track_id, light_response_true_photons, trigger_idx, op_channel_idx,
                    digit_samples, light_noise)
                RangePop()

                light_event_id_list.append(cp.full(trigger_idx.shape[0], unique_eventIDs[0])) # FIXME: only works if looping on a single event
                light_start_time_list.append(cp.full(trigger_idx.shape[0], light_t_start))
                light_trigger_idx_list.append(trigger_idx)
                light_op_channel_idx_list.append(op_channel_idx)
                light_waveforms_list.append(light_digit_signal)
                light_waveforms_true_track_id_list.append(light_digit_signal_true_track_id)
                light_waveforms_true_photons_list.append(light_digit_signal_true_photons)

        if event_id_list and adc_tot_list and (len(event_id_list) > EVENT_BATCH_SIZE or ievd == tot_evids.shape[0]-1):
            event_id_list_batch = np.concatenate(event_id_list, axis=0)
            adc_tot_list_batch = np.concatenate(adc_tot_list, axis=0)
            adc_tot_ticks_list_batch = np.concatenate(adc_tot_ticks_list, axis=0)
            unique_pix_tot_batch = np.concatenate(unique_pix_tot, axis=0)
            current_fractions_tot_batch = np.concatenate(current_fractions_tot, axis=0)
            track_pixel_map_tot_batch = np.concatenate(track_pixel_map_tot, axis=0)

            if light.LIGHT_SIMULATED:
                light_event_id_list_batch = np.concatenate(light_event_id_list, axis=0)
                light_start_time_list_batch = np.concatenate(light_start_time_list, axis=0)
                light_trigger_idx_list_batch = np.concatenate(light_trigger_idx_list, axis=0)
                light_op_channel_idx_list_batch = np.concatenate(light_op_channel_idx_list, axis=0)
                light_waveforms_list_batch = np.concatenate(light_waveforms_list, axis=0)
                light_waveforms_true_track_id_list_batch = np.concatenate(light_waveforms_true_track_id_list, axis=0)
                light_waveforms_true_photons_list_batch = np.concatenate(light_waveforms_true_photons_list, axis=0)
                light_trigger_modules = cp.array([detector.TPC_TO_MODULE[tpc] for tpc in light.OP_CHANNEL_TO_TPC[light_op_channel_idx_list_batch.get()][:,0]])

            fee.export_to_hdf5(event_id_list_batch,
                               adc_tot_list_batch,
                               adc_tot_ticks_list_batch,
                               cp.asnumpy(unique_pix_tot_batch),
                               cp.asnumpy(current_fractions_tot_batch),
                               cp.asnumpy(track_pixel_map_tot_batch),
                               output_filename,
                               event_times[np.unique(event_id_list_batch)],
                               is_first_event=last_time==0,
                               light_trigger_times=event_times[np.unique(event_id_list_batch)] if not light.LIGHT_SIMULATED else light_start_time_list_batch + light_trigger_idx_list_batch * light.LIGHT_TICK_SIZE,
                               light_trigger_event_id=np.unique(event_id_list_batch) if not light.LIGHT_SIMULATED else light_event_id_list_batch,
                               light_trigger_modules=np.ones(len(np.unique(event_id_list_batch))) if not light.LIGHT_SIMULATED else light_trigger_modules,
                               bad_channels=bad_channels)

            event_id_list = []
            adc_tot_list = []
            adc_tot_ticks_list = []
            unique_pix_tot = []
            current_fractions_tot = []
            track_pixel_map_tot = []

            if light.LIGHT_SIMULATED:
                light_sim.export_to_hdf5(light_event_id_list_batch,
                                         light_start_time_list_batch,
                                         light_trigger_idx_list_batch,
                                         light_op_channel_idx_list_batch,
                                         light_waveforms_list_batch,
                                         output_filename,
                                         event_times[np.unique(light_event_id_list_batch)],
                                         light_waveforms_true_track_id_list_batch,
                                         light_waveforms_true_photons_list_batch)

                light_event_id_list = []
                light_start_time_list = []
                light_trigger_idx_list = []
                light_op_channel_idx_list = []
                light_waveforms_list = []
                light_waveforms_true_track_id_list_batch = []
                light_waveforms_true_photons_list_batch = []

            last_time = event_times[-1]

    with h5py.File(output_filename, 'a') as output_file:
        if 'configs' in output_file.keys():
            output_file['configs'].attrs['pixel_layout'] = pixel_layout

    print("Output saved in:", output_filename)

    RangePop()
    end_simulation = time()
    print(f"Elapsed time: {end_simulation-start_simulation:.2f} s")

if __name__ == "__main__":
    fire.Fire(run_simulation)
