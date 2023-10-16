"""
Set global variables with detector and physics properties
"""
from . import detector, light, physics

def load_properties(detprop_file, pixel_file, physics_file):
    """
    The function loads the detector properties,
    the pixel geometry, and the physics constants YAML files
    and stores the constants as global variables

    Args:
        detprop_file (str): detector properties YAML filename
        pixel_file (str): pixel layout YAML filename
        physics_file (str): physics constants YAML filename
    """
    detector.set_detector_properties(detprop_file, pixel_file)
    light.set_light_properties(detprop_file)
    physics.set_physics(physics_file)
