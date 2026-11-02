"""Robot-only drive properties, read back after native actuator initialization."""
from .native_compat import array


def drive_parameters(robot, arm_names):
    names = [*arm_names, "finger_joint"]
    ids, resolved = robot.find_joints(names, preserve_order=True)
    if list(resolved) != names:
        raise ValueError("Cannot resolve the DROID driven joints in order")
    view = robot.root_physx_view
    stiffness = array(view.get_dof_stiffnesses())[0]
    damping = array(view.get_dof_dampings())[0]
    limits = array(view.get_dof_max_forces())[0]
    return {
        "source": "Initialized PhysX articulation drive readback",
        "drive_type": "force",
        "units": {"stiffness": "N*m/rad", "damping": "N*m*s/rad", "effort_limit": "N*m"},
        "description": "Angular joint position drives. Gains act on joint angle/velocity error, "
                       "not directly on jaw opening in meters. Effort limits are joint torque limits, "
                       "not fingertip contact forces. Passive mimic constraints are not drive gains.",
        "joints": {name: {"stiffness": float(stiffness[index]),
                          "damping": float(damping[index]),
                          "effort_limit": float(limits[index])}
                   for name, index in zip(names, ids)},
    }
