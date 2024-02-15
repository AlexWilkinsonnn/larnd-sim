"""
Set constants controlling electronics response
"""
import yaml

from .units import mV, e

## Electronics params used in fee.py
#: Maximum number of ADC values stored per pixel
MAX_ADC_VALUES = 10
#: Discrimination threshold in e-
DISCRIMINATION_THRESHOLD = 7e3 * e
#: ADC hold delay in clock cycles
ADC_HOLD_DELAY = 15
#: ADC busy delay in clock cycles
ADC_BUSY_DELAY = 9
#: Reset time in clock cycles
RESET_CYCLES = 1
#: Clock cycle time in :math:`\mu s`
CLOCK_CYCLE = 0.1
#: Clock rollover / reset time in larpix clock ticks
ROLLOVER_CYCLES = 2**31
#: Front-end gain in :math:`mV/e-`
GAIN = 4 * mV / (1e3 * e)
#: Buffer risetime in :math:`\mu s` (set >0 to include buffer response simulation)
BUFFER_RISETIME = 0.100
#: Common-mode voltage in :math:`mV`
V_CM = 288 * mV
#: Reference voltage in :math:`mV`
V_REF = 1300 * mV
#: Pedestal voltage in :math:`mV`
V_PEDESTAL = 580 * mV
#: Number of ADC counts
ADC_COUNTS = 2**8
#: Reset noise in e-
RESET_NOISE_CHARGE = 900 * e
#: Uncorrelated noise in e-
UNCORRELATED_NOISE_CHARGE = 500 * e
#: Discriminator noise in e-
DISCRIMINATOR_NOISE = 650 * e
#: Average time between events in microseconds
EVENT_RATE = 100000 # 10Hz

def set_electronics(electronics_file):
    """
    The function loads the electronics constants YAML file
    and stores the constants as global variables

    Args:
        electronics_file (str): electronics constants YAML filename
    """
    global MAX_ADC_VALUES
    global DISCRIMINATION_THRESHOLD
    global ADC_HOLD_DELAY
    global ADC_BUSY_DELAY
    global RESET_CYCLES
    global CLOCK_CYCLE
    global ROLLOVER_CYCLES
    global GAIN
    global BUFFER_RISETIME
    global V_CM
    global V_REF
    global V_PEDESTAL
    global ADC_COUNTS
    global RESET_NOISE_CHARGE
    global UNCORRELATED_NOISE_CHARGE
    global DISCRIMINATOR_NOISE
    global EVENT_RATE

    with open(electronics_file) as ef:
        elec = yaml.load(ef, Loader=yaml.FullLoader)

    MAX_ADC_VALUES = elec["max_adc_values"]
    DISCRIMINATION_THRESHOLD = elec["discrimination_threshold"] * e
    ADC_HOLD_DELAY = elec["adc_hold_delay"]
    ADC_BUSY_DELAY = elec["adc_busy_delay"]
    RESET_CYCLES = elec["reset_cycles"]
    CLOCK_CYCLE = elec["clock_cycle"]
    ROLLOVER_CYCLES = elec["rollover_cycles"]
    GAIN = elec["gain"] * mV / (1e3 * e)
    BUFFER_RISETIME = elec["buffer_risetime"]
    V_CM = elec["v_cm"] * mV
    V_REF = elec["v_ref"] * mV
    V_PEDESTAL = elec["v_pedestal"] * mV
    ADC_COUNTS = elec["adc_counts"]
    RESET_NOISE_CHARGE = elec["reset_noise_charge"] * e
    UNCORRELATED_NOISE_CHARGE = elec["uncorrelated_noise_charge"] * e
    DISCRIMINATOR_NOISE = elec["discriminator_noise"] * e
    EVENT_RATE = elec["event_rate"]

