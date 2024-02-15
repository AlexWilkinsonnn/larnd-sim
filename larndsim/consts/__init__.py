"""
Set global variables with detector, physics, and electronics properties
"""
from . import detector, light, physics, electronics

def load_properties(detprop_file, pixel_file, physics_file, electronics_file):
    """
    The function loads the detector properties, the pixel geometry, the physics constants,
    and the electronics constants YAML files and stores the constants as global variables

    Args:
        detprop_file (str): detector properties YAML filename
        pixel_file (str): pixel layout YAML filename
        physics_file (str): physics constants YAML filename
        electronics_file (str): electronics constants YAML filename
    """
    detector.set_detector_properties(detprop_file, pixel_file)
    light.set_light_properties(detprop_file)
    physics.set_physics(physics_file)
    electronics.set_electronics(electronics_file)
