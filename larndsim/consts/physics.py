"""
Set physics constants
"""
import yaml

## Physical params
#: Recombination :math:`\alpha` constant for the Box model
BOX_ALPHA = 0.93
#: Recombination :math:`\beta` value for the Box model in :math:`(kV/cm)(g/cm^2)/MeV`
BOX_BETA = 0.207 #0.3 (MeV/cm)^-1 * 1.383 (g/cm^3)* 0.5 (kV/cm), R. Acciarri et al JINST 8 (2013) P08005
#: Recombination :math:`A_b` value for the Birks Model
BIRKS_Ab = 0.800
#: Recombination :math:`k_b` value for the Birks Model in :math:`(kV/cm)(g/cm^2)/MeV`
BIRKS_kb = 0.0486 # g/cm2/MeV Amoruso, et al NIM A 523 (2004) 275
#: Electron charge in Coulomb
E_CHARGE = 1.602e-19
#: Average energy expended per ion pair in LAr in :math:`MeV` from Phys. Rev. A 10, 1452
W_ION = 23.6e-6

## Quenching parameters
BOX = 1
BIRKS = 2
QUENCHING_MODEL = BIRKS

def set_physics(physics_file):
    """
    The function loads the physics constants YAML file
    and stores the constants as global variables

    Args:
        physics_file (str): physics constants YAML filename
    """
    global BOX_ALPHA
    global BOX_BETA
    global BIRKS_Ab
    global BIRST_kb
    global E_CHARGE
    global W_ION
    global QUENCHING_MODEL

    with open(physics_file) as pf:
        phys = yaml.load(pf, Loader=yaml.FullLoader)

    BOX_ALPHA = phys["box_alpha"]
    BOX_BETA = phys["box_beta"]
    BIRKS_Ab = phys["birks_Ab"]
    BIRKS_kb = phys["birks_kb"]
    E_CHARGE = phys["e"]
    W_ION = phys["w_ion"]
    QUENCHING_MODEL = phys["quenching_model"]

