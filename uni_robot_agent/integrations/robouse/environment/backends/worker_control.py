"""Native pose/aperture conversion for the pinned subprocess backends."""
import numpy as np
from ..control import state, rotation, fixed_rotation, axis_angle, latched_grip
from uni_robot_agent.services.environment.contracts import ArmControlCapabilities, RequestRejected


def encode(family, target, current):
    position = np.asarray(target.pose.position)
    quat = target.pose.quaternion
    if family == "robocasa":
        fixed_rotation(quat, current["target_quaternion"])
        return np.clip(np.r_[(position-current["target_position"])/.02, latched_grip(target.gripper)], -1, 1).tolist()
    if family == "factory":
        if abs(target.gripper-current["held_opening"]) > .001:
            raise RequestRejected("Factory starts pregrasped; changing the finger aperture is unavailable")
        # Factory projects roll/pitch to [pi, 0]; only world yaw can be commanded.
        axis_angle(quat, [0., 1., 0., 0.], 2, np.pi)
        yaw = (rotation(quat)*rotation(current["quaternion"]).inv()).as_rotvec()[2]
        if current["clockwise_only"] and yaw > .001:
            raise RequestRejected("Factory nut threading accepts clockwise yaw only")
        desired = np.r_[(position-current["position"])/current["position_scale"], 0., 0., yaw/current["rotation_scale"][2]]
        desired = np.clip(desired, -1, 1)
        if current["clockwise_only"]:
            desired[5] = -2 * desired[5] - 1
        # Invert native EMA where feasible; saturation retains native acceleration limits.
        alpha = current["ema"]
        return np.clip((desired-(1-alpha)*np.asarray(current["previous_action"]))/alpha, -1, 1).tolist()
    origin = current["target_position"] if family == "maniskill_cpu" else current["position"]
    reference = current["target_quaternion"] if family == "maniskill_cpu" else current["quaternion"]
    pscale, rscale = {"maniskill_cpu": (.02,.1), "maniskill_gpu": (.1,.1), "genesis": (.01,np.deg2rad(3))}[family]
    delta = (rotation(quat)*rotation(reference).inv()).as_rotvec()/rscale
    translation = (position-origin)/pscale
    if family == "maniskill_gpu":
        base = rotation(current["base_quaternion"])
        translation = base.inv().apply(translation)
        relative = base.inv()*rotation(quat)*rotation(reference).inv()*base
        # ManiSkill 3.0.1 uses intrinsic XYZ Euler increments and multiplies
        # normalized rotation by rot_lower (negative), despite the upstream doc.
        delta = relative.as_euler("XYZ")/current["rotation_scale"]
        delta /= max(1., np.linalg.norm(delta))
    if family == "genesis":
        if target.gripper not in (0., 1.):
            raise RequestRejected("Genesis exposes binary gripper opening only; use aperture 0 or 1")
        grip = 2*target.gripper-1
    elif family == "maniskill_cpu":
        grip = latched_grip(target.gripper)
    else:
        grip = 2*target.gripper-1
    return np.clip(np.r_[translation, delta, grip], -1, 1).tolist()


class WorkerCamera:
    def __init__(self, backend, cameras, depth):
        self.b, self.cameras, self.depth = backend, dict.fromkeys(cameras), depth
        unknown = set(cameras)-set(backend.meta["cameras"])
        if unknown:
            raise ValueError(f"Unavailable native cameras: {sorted(unknown)}")

    def capture(self, names):
        return self.b.capture(names, self.depth)

    def close(self):
        pass  # Native camera ownership belongs to the child process.


class WorkerControl:
    names = ("arm",)

    def __init__(self, backend):
        self.b = backend
        self.hz = 15. if backend.family == "factory" else 20.
        self.actor_control = (ArmControlCapabilities(commands=["pose"], rotation="fixed", rotation_reference="target")
                              if backend.family == "robocasa" else None)
        self.control_point = "robocasa_tcp" if backend.family == "robocasa" else "native_tcp"
        self.constraint = {
            "maniskill_cpu": "Full world TCP pose; latched finger aperture.",
            "maniskill_gpu": "Full world TCP pose; absolute finger aperture.",
            "robocasa": "Fixed base and torso. Translation only; preserve target orientation.",
            "genesis": "Full world TCP pose. Binary aperture: 0 closed or 1 open only.",
            "factory": "Pregrasped part: preserve initial aperture. Roll=pi, pitch=0; nut yaw clockwise only. Native EMA and workspace clamp apply.",
        }[backend.family]

    def read(self, commands):
        c = self.b.control()
        measured = state(c["position"], c["quaternion"], c["opening"], commands["arm"])
        if self.b.family == "robocasa":
            # Measured orientation may drift; native translation retains this drive target.
            measured.metadata["target_quaternion"] = list(c["target_quaternion"])
        return {"arm": measured}

    def encode(self, targets):
        return encode(self.b.family, targets["arm"], self.b.control())
