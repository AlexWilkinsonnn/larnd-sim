"""
Detector constants
"""
lArDensity = 1.38 # g/cm^3
eField = 0.50 # kV/cm

"""
Unit Conversions
"""
GeVToMeV = 1e3 # MeV
MeVToElectrons = 4.237e+04
msTous = 10e3 # us

"""
PHYSICAL_PARAMS
"""
MeVToElectrons = 4.237e+04
alpha = 0.847
beta = 0.2061
Ab = 0.800
kb = 0.0486 # g/cm2/MeV Amoruso, et al NIM A 523 (2004) 275

"""
TPC_PARAMS
"""
vdrift = 0.153812 # cm / us,
lifetime = 10e3 # us,
tpcBorders = ((-50, 50), (-50, 50), (-50, 50)) # cm,
tpcZStart = -50 # cm
timeInterval = (0, 3000) # us
longDiff = 6.2e-6 # cm * cm / us,
tranDiff = 16.3e-6 # cm

