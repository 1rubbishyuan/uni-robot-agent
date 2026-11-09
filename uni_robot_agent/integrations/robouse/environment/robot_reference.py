"""Robot-only model selection for pinned RoboUse native backends."""
from uni_robot_agent.integrations.robot_resources import mujoco_robot_model, urdf_kinematics


def direct_model(binding):
    model, backend, kind = binding.model, binding.b, binding.kind
    if kind in {"libero", "robosuite"}:
        bodies = [model.site_bodyid[entry[1]] for entry in binding.entries]
    elif kind == "metaworld":
        bodies = [model.body("hand").id]
    else:
        if kind == "tabletop":
            sites = ["tcp"]
        elif kind == "gymrobotics":
            sites = ["robot0:grip"]
        elif kind == "kitchen":
            sites = [backend._ee]
        elif kind == "menagerie":
            sites = [backend.scene.arms[name].tcp for name in binding.names]
        elif kind == "components":
            sites = [backend.driver.cfg[name].tcp for name in binding.names]
        else:
            raise ValueError(f"No robot model binding for {kind}")
        bodies = [model.site_bodyid[model.site(site).id] for site in sites]
    return mujoco_robot_model(model, bodies)


def worker_model(worker):
    # Read native robot models in their owner process, never from the planner.
    from .native_worker import array
    native, family = worker.native, worker.family
    if family in {"maniskill_cpu", "maniskill_gpu"}:
        agent = native.u.agent
        result = urdf_kinematics(agent.urdf_path)
        result["control"] = {"tcp_link": agent.ee_link_name,
            "arm_joint_names": list(agent.arm_joint_names), "gripper_joint_names": list(agent.gripper_joint_names),
            "base_position": array(agent.robot.pose.p)[0].tolist(),
            "base_quaternion_wxyz": array(agent.robot.pose.q)[0].tolist()}
        return result
    if family == "robocasa":
        model = native.env.sim.model._model
        bodies = [model.body(robot.robot_model.root_body).id for robot in native.env.robots]
        return mujoco_robot_model(model, bodies)
    if family == "genesis":
        # The admitted Genesis backend loads this robot-only MJCF. Read the same
        # asset, not the task scene or the MuJoCo camera proxy used by Factory.
        import genesis as gs
        import mujoco
        from pathlib import Path
        path = Path(gs.__file__).parent / "assets/xml/franka_emika_panda/panda.xml"
        model = mujoco.MjModel.from_xml_path(str(path))
        return mujoco_robot_model(model, [model.body("hand").id])
    if family == "factory":
        robot = native.u._robot
        return {"format": "isaaclab_articulation_limits_v1", "joint_names": list(robot.joint_names),
            "body_names": list(robot.body_names), "joint_position_limits": array(robot.data.joint_pos_limits)[0].tolist(),
            "units": "metres for prismatic joints, radians for revolute joints",
            "limitations": "Native articulation names and limits only; USD meshes and kinematic transforms are not exported.",
            "control": "Fingertip midpoint pose; gripper opening remains fixed at its initial command."}
    raise ValueError(f"No robot model binding for {family}")
