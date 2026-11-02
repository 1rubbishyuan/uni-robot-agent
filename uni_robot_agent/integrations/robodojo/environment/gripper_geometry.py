"""X5 physical pad separation and native preload-aware opening calibration."""
import numpy as np

from .kinematics import T_NATIVE_FROM_GRASP

AUDITED_ASSETS = {
    "X5A.urdf": "a31a0d3a957196ea42d216036db2fffdf7678f4f36f65f90a0b69c58daa8d7d0",
    "meshes/link7.STL": "ec3d40b39cccde0075ca3d16603f14f180fce41a46b446473d384def7a1b0be0",
    "meshes/link8.STL": "29f7a8379cad81797e82e4ba099455cc275ac6ac5bb9e264ce46719fd7d2bb60",
}


class X5Gripper:
    def __init__(self, geometry, *, scale, limits, mimic, sign):
        if any(geometry["asset_sha256"].get(key) != value for key, value in AUDITED_ASSETS.items()):
            raise ValueError("X5 pad calibration requires audited URDF and finger assets")
        if sign != 1 or list(mimic) != ["joint8", 1., 0.]:
            raise ValueError("X5 calibration requires opposed, equally driven fingers")
        self.pads = {name: np.asarray(geometry["pads"][name]["triangles"]).reshape(-1, 3)
                     for name in ("link7", "link8")}
        joints = {j["child"]: j for j in geometry["joints"]}
        centers, self.frames = [], {}
        for name, axis in (("link7", [0, 1, 0]), ("link8", [0, -1, 0])):
            joint = joints[name]
            frame = np.asarray(joint["T_parent_from_joint"])
            if joint["type"] != "prismatic" or joint["parent"] != "link6" or joint["axis"] != axis or not np.allclose(frame[:3, :3], np.eye(3)):
                raise ValueError("Unsupported X5 finger kinematics")
            self.frames[name] = frame
            centers.append(frame[:3, 3] + geometry["pads"][name]["centroid"])
        if not np.allclose(np.mean(centers, axis=0), T_NATIVE_FROM_GRASP[:3, 3], atol=1e-6, rtol=0):
            raise ValueError("Finger pads disagree with the registered grasp site")
        self.zero_gap = self.measure({"link6": np.eye(4), **self.frames})
        limits = np.asarray(limits, dtype=float)
        scale = np.asarray(scale, dtype=float)
        if limits.shape != (2, 2) or not np.isfinite(limits).all() or np.any(limits[:, 1] <= limits[:, 0]):
            raise ValueError("Expected physical limits for both X5 fingers")
        if scale.shape != (2,) or not np.isfinite(scale).all() or scale[1] <= scale[0] or not np.allclose(limits[:, 1], scale[1]):
            raise ValueError("X5 command scale must end at the physical open limit")
        self.model = {"kind": "affine_pad_gap_v1", "command_offset_m": self.zero_gap + 2 * scale[0],
                      "command_scale_m": 2 * (scale[1] - scale[0]), "closed_command": 0., "open_command": 1.,
                      "min_opening_m": self.zero_gap + float(limits[:, 0].sum()),
                      "max_opening_m": self.zero_gap + float(limits[:, 1].sum())}
        self.summary = {"frame": "grasp_site", "opening_model": self.model,
                        "measurement": "Signed inward pad gap along link6 +Y from actual link poses",
                        "closed_behavior": "Below the geometric minimum, keep native closing preload; zero is a close request"}
        site_inverse = np.linalg.inv(T_NATIVE_FROM_GRASP)
        self.summary["contact_geometry"] = {
            "frame": "grasp_site", "units": "meters", "reference_opening_m": self.zero_gap,
            "configuration": "Equal opposed finger displacement inferred from full gap; not measured individual finger poses",
            "fingers": {},
        }
        for name, direction in (("link7", [0., 1., 0.]), ("link8", [0., -1., 0.])):
            frame = site_inverse @ self.frames[name]
            pad = self.pads[name] @ frame[:3, :3].T + frame[:3, 3]
            box = np.asarray(geometry["link_local_bounds_m"][name])
            corners = np.array([[x, y, z] for x in box[:, 0] for y in box[:, 1] for z in box[:, 2]])
            corners = corners @ frame[:3, :3].T + frame[:3, 3]
            self.summary["contact_geometry"]["fingers"][name] = {
                "pad_bounds_m": [pad.min(axis=0).tolist(), pad.max(axis=0).tolist()],
                "finger_bounds_m": [corners.min(axis=0).tolist(), corners.max(axis=0).tolist()],
                "translation_per_gap_m": (np.asarray(direction) / 2).tolist(),
            }
        for name in ("link6", "link7", "link8"):
            box = np.asarray(geometry["link_local_bounds_m"][name])
            frame = np.eye(4) if name == "link6" else self.frames[name]
            self.summary[name + "_bounds_at_zero_joint_m"] = (box + (site_inverse @ frame)[:3, 3]).tolist()

    def measure(self, body_poses):
        inverse = np.linalg.inv(body_poses["link6"])
        edges = {}
        for name, vertices in self.pads.items():
            frame = inverse @ body_poses[name]
            y = vertices @ frame[1, :3] + frame[1, 3]
            edges[name] = y.min() if name == "link7" else y.max()
        return float(edges["link7"] - edges["link8"])

    def target_opening(self, command):
        return float(np.clip(self.model["command_offset_m"] + self.model["command_scale_m"] * command,
                             self.model["min_opening_m"], self.model["max_opening_m"]))
