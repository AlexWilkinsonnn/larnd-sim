#!/usr/bin/env python

import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir) 

from larndsim import consts, quenching, drifting, detsim, pixels_from_track, fee
from larndsim import indeces as i

import numpy as np
import fire

import matplotlib.pyplot as plt
import matplotlib as mpl
import matplotlib.cm as cm
from mpl_toolkits.axes_grid1 import make_axes_locatable

import pickle
import numpy as np
import numba as nb
import pandas as pd

from numba import cuda
from math import ceil, sqrt, pi, exp, erf
from time import time

from tqdm import tqdm

def run_simulation(input_filename='../examples/qc_tpc_10k.p', 
                   output_filename='qc_tpc_cosmics.h5',
                   pixel_geometry='../examples/pixel_geometry.yaml',
                   n_tracks=1000):
    consts.load_pixel_geometry(pixel_geometry)
    
    tracks_np = pickle.load(open(input_filename, 'rb'))
    x_start, x_end = np.copy(tracks_np[:,i.x_start]), np.copy(tracks_np[:,i.x_end])
    z_start, z_end = np.copy(tracks_np[:,i.z_start]), np.copy(tracks_np[:,i.z_end])
    tracks_np[:,i.z_start], tracks_np[:,i.z_end] = x_start, x_end
    tracks_np[:,i.x_start], tracks_np[:,i.x_end] = z_start, z_end

    d_tracks_np = cuda.to_device(tracks_np)
    
    TPB = 256
    BPG = ceil(tracks_np.shape[0] / TPB)

    print("Quenching electrons...",end='')
    start_quenching = time()
    quenching.quench[BPG,TPB](d_tracks_np, consts.box)
    end_quenching = time()
    print(f" {end_quenching-start_quenching:.2f} s")
    
    print("Drifting electrons...",end='')
    start_drifting = time()
    drifting.drift[BPG,TPB](d_tracks_np)
    end_drifting = time()
    print(f" {end_drifting-start_drifting:.2f} s")
        
    step = 1000
    adc_tot_list = np.empty((1,fee.MAX_ADC_VALUES))
    adc_tot_ticks_list = np.empty((1,fee.MAX_ADC_VALUES))
    unique_pix_tot = np.empty((1,2))
    tot_events = 0
    d_tracks_np = d_tracks_np[:n_tracks]
    for itrk in tqdm(range(0,d_tracks_np.shape[0],step),desc='Simulating pixels...'):
        selected_tracks = d_tracks_np[itrk:itrk+step]

        MAX_PIXELS = 50
        MAX_ACTIVE_PIXELS = 20
        active_pixels = np.zeros(shape=(selected_tracks.shape[0], MAX_ACTIVE_PIXELS, 2))
        neighboring_pixels = np.full((selected_tracks.shape[0], MAX_PIXELS, 2), -1, dtype=np.int32)
        d_active_pixels = cuda.to_device(active_pixels)
        n_pixels_list = np.zeros(shape=(selected_tracks.shape[0]))
        TPB = 128
        BPG = ceil(selected_tracks.shape[0] / TPB)
        pixels_from_track.get_pixels[BPG,TPB](selected_tracks, 
                                              d_active_pixels, 
                                              neighboring_pixels, 
                                              n_pixels_list)


        unique_eventIDs = np.unique(selected_tracks[:,i.eventID])
        event_id_map = np.zeros_like(selected_tracks[:,i.eventID],dtype=np.int32)
        for iev, evID in enumerate(selected_tracks[:,i.eventID]):
            event_id_map[iev] = np.where(evID == unique_eventIDs)[0][0]
        d_event_id_map = cuda.to_device(event_id_map)

        # Here we calculate the track start times and the longest signal time
        max_length = np.array([0])
        track_starts = np.empty(selected_tracks.shape[0])
        d_track_starts = cuda.to_device(track_starts)
        TPB = 128
        BPG = ceil(selected_tracks.shape[0] / TPB)
        detsim.time_intervals[BPG,TPB](d_track_starts, max_length,  d_event_id_map, selected_tracks)

        # Here we calculate the induced current signals
        signals = np.zeros((selected_tracks.shape[0], 
                            neighboring_pixels.shape[1], 
                            max_length[0]), dtype=np.float32)
        TPB = (4,4,4)
        blockspergrid_x = ceil(signals.shape[0] / TPB[0])
        blockspergrid_y = ceil(signals.shape[1] / TPB[1])
        blockspergrid_z = ceil(signals.shape[2] / TPB[2])
        BPG = (blockspergrid_x, blockspergrid_y, blockspergrid_z)
        d_signals = cuda.to_device(signals)

        detsim.tracks_current[BPG,TPB](d_signals, neighboring_pixels, selected_tracks)

        shapes = neighboring_pixels.shape
        joined = neighboring_pixels.reshape(shapes[0]*shapes[1],2)
        unique_pix = np.unique(joined, axis=0)[1:]
        pixel_index_map = np.full((selected_tracks.shape[0], neighboring_pixels.shape[1]), -1)

        for itr in range(neighboring_pixels.shape[0]):
            for ipix in range(neighboring_pixels.shape[1]):
                pID = neighboring_pixels[itr][ipix]
                if pID[0] >= 0 and pID[1] >= 0:
                    try:
                        index = np.where((unique_pix[:,0] == pID[0]) & (unique_pix[:,1] == pID[1]))[0][0]
                    except IndexError:
                        print("More pixels than maximum value")
                    pixel_index_map[itr,ipix] = index

        d_pixel_index_map = cuda.to_device(pixel_index_map)
        pixels_signals = np.zeros((len(unique_pix),len(consts.time_ticks)*len(unique_eventIDs)*3))
        d_pixels_signals = cuda.to_device(pixels_signals)
        TPB = (4,4,4)
        blockspergrid_x = ceil(d_signals.shape[0] / TPB[0])
        blockspergrid_y = ceil(d_signals.shape[1] / TPB[1])
        blockspergrid_z = ceil(d_signals.shape[2] / TPB[2])
        BPG = (blockspergrid_x, blockspergrid_y, blockspergrid_z)
        detsim.sum_pixel_signals[BPG,TPB](d_pixels_signals, 
                                          d_signals, 
                                          d_track_starts, 
                                          d_pixel_index_map)
        
        time_ticks = np.linspace(0,len(unique_eventIDs)*consts.time_interval[1]*3,d_pixels_signals.shape[1]+1)
        integral_list = np.zeros((d_pixels_signals.shape[0], fee.MAX_ADC_VALUES))
        adc_ticks_list = np.zeros((d_pixels_signals.shape[0], fee.MAX_ADC_VALUES))
        TPB = 32
        BPG = ceil(d_pixels_signals.shape[0] / TPB)
        fee.get_adc_values[BPG,TPB](d_pixels_signals, 
                                    time_ticks, 
                                    integral_list, 
                                    adc_ticks_list, 
                                    consts.time_interval[1]*3*tot_events)
        adc_list = fee.digitize(integral_list)
        adc_tot_list = np.append(adc_tot_list, adc_list,axis=0)
        adc_tot_ticks_list = np.append(adc_tot_ticks_list, adc_ticks_list,axis=0)
        unique_pix_tot = np.append(unique_pix_tot, unique_pix, axis=0)
        
        tot_events += len(unique_eventIDs)

    unique_pix_tot = unique_pix_tot[1:]
    adc_tot_list = adc_tot_list[1:]
    adc_tot_ticks_list = adc_tot_ticks_list[1:]
    fee.export_to_hdf5(adc_tot_list, adc_tot_ticks_list, unique_pix_tot, output_filename)
    
if __name__ == "__main__":
    fire.Fire(run_simulation)