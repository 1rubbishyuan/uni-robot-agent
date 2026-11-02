"""Lossless camera transport over RoboUse's JSON worker channel."""
import base64
import io
import numpy as np
from uni_robot_agent.services.environment.snapshots import CameraFrame


def pack_frames(frames):
    result = {}
    for name, frame in frames.items():
        values = {"rgb": frame.rgb}
        for key in ("depth_m", "intrinsic_K", "T_world_from_optical"):
            value = getattr(frame, key)
            if value is not None:
                values[key] = value
        stream = io.BytesIO()
        np.savez_compressed(stream, **values)
        result[name] = {"arrays": base64.b64encode(stream.getvalue()).decode(), "calibration": frame.calibration}
    return result


def unpack_frames(payload):
    result = {}
    for name, item in payload.items():
        with np.load(io.BytesIO(base64.b64decode(item["arrays"])), allow_pickle=False) as arrays:
            result[name] = CameraFrame(arrays["rgb"], arrays.get("depth_m"), arrays.get("intrinsic_K"),
                                      arrays.get("T_world_from_optical"), item["calibration"])
    return result
