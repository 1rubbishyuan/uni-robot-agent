"""Project native robot sensors without exposing task predicates or object state."""
import numpy as np

from uni_robot_agent.services.environment.snapshots import CameraFrame
from .native_compat import CAMERAS, array

POSITION_SLICES = {"trunk": (53, 57), "left_arm": (3, 10), "left_gripper": (24, 26),
                   "right_arm": (28, 35), "right_gripper": (49, 51)}


def project_observation(native):
    values = array(native.observation[f"{native.robot.name}::proprio"]).astype(float)
    if values.shape != (61,) or not np.isfinite(values).all():
        raise ValueError("Expected the native finite 61D R1Pro proprioception vector")
    devices = {"robot": {"proprioception": values.tolist()},
               "base": {"frame": "base", "linear_m_s": [*values[:2].tolist(), 0.],
                        "angular_rad_s": [0., 0., float(values[2])]}}
    for name, (start, stop) in POSITION_SLICES.items():
        spec = native.controllers[name]
        devices[name] = {"joint_positions": dict(zip(spec["joint_names"], values[start:stop].tolist(), strict=True)),
                         "joint_unit": spec["unit"]}
    frames = {}
    for name in CAMERAS:
        rgb = array(native.observation[native.camera_names[name] + "::rgb"])
        if rgb.shape != (224, 224, 3) and rgb.shape != (224, 224, 4):
            raise ValueError("Expected native 224x224 R1Pro RGB cameras")
        frames[name] = CameraFrame(rgb[:, :, :3].copy())
    return devices, frames
