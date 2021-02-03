#!/usr/bin/env python

import numpy as np
import pytest

from math import ceil

from larndsim import drifting
from larndsim import consts
from larndsim import indeces as i

consts.load_pixel_geometry("larndsim/pixel_layouts/layout-2.5.0.yaml")

class TestDrifting:

    tracks = np.zeros((1, 29))
    tracks[:, i.z] = np.random.uniform(consts.module_borders[0][2][0], consts.module_borders[0][2][1], 1)
    tracks[:, i.x] = np.random.uniform(consts.module_borders[0][0][0], consts.module_borders[0][0][1], 1)
    tracks[:, i.y] = np.random.uniform(consts.module_borders[0][1][0], consts.module_borders[0][1][1], 1)
    tracks[:, i.n_electrons] = np.random.uniform(1e6, 1e7, 1)

    def test_lifetime(self):

        zAnode = consts.module_borders[0][2][0]

        driftDistance = np.abs(self.tracks[:, i.z] - zAnode)
        driftTime = driftDistance / consts.vdrift

        lifetime = np.exp(-driftTime / consts.lifetime)

        tracks = self.tracks
        electronsAtAnode = tracks[:, i.n_electrons] * lifetime

        TPB = 128
        BPG = ceil(tracks.shape[0] / TPB)
        drifting.drift[BPG,TPB](tracks)

        assert tracks[:, i.n_electrons] == pytest.approx(electronsAtAnode)
