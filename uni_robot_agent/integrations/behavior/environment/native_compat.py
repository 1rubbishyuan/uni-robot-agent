"""Pinned OmniGibson evaluation setup without its policy or rollout loop."""
import hashlib
import math
import sys
from pathlib import Path

import numpy as np

REVISION = "fe66cc74538f7eba3c57255da0ee88e8ce113c87"
CONTROL_DT = 1 / 30
CAMERAS = ("head", "left_wrist", "right_wrist")
COMPONENTS = {"trunk": (3, 7), "left_arm": (7, 14), "left_gripper": (14, 15),
              "right_arm": (15, 22), "right_gripper": (22, 23)}
CONTROLLERS = {"trunk": "trunk", "left_arm": "arm_left", "right_arm": "arm_right",
               "left_gripper": "gripper_left", "right_gripper": "gripper_right"}


def array(value):
    return np.asarray(value.detach().cpu().numpy() if hasattr(value, "detach") else value)


def json_metrics(value):
    """Preserve native non-finite metric values in strict JSON outcomes."""
    if isinstance(value, dict):
        return {key: json_metrics(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_metrics(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return value


def validate_source(path):
    from uni_robot_agent.integrations.simulation import source_root
    root = source_root(Path(path), REVISION)
    sys.path.insert(0, str(root / "OmniGibson"))
    return root


def controller_description(robot):
    """Read instantiated controllers, including resolved default gripper limits."""
    from omnigibson.controllers import ControlType
    from omnigibson.controllers.controller_view import ControllerView
    from omnigibson.utils.backend_utils import _compute_backend as cb

    if robot.model != "r1pro" or robot.action_dim != 23 or robot.grasping_mode != "assisted":
        raise ValueError("Expected the official 23D R1Pro assisted-grasp configuration")
    indices = {name: array(values).tolist() for name, values in robot.controller_action_idx.items()}
    expected = {CONTROLLERS[name]: list(range(*span)) for name, span in COMPONENTS.items()}
    expected["base"] = [0, 1, 2]
    if any(indices.get(name) != values for name, values in expected.items()):
        raise ValueError("R1Pro native controller action ordering changed")
    result = {}
    for name, controller_name in {"base": "base", **CONTROLLERS}.items():
        key, _ = robot.controllers[controller_name]
        controller = ControllerView._controller_groups[key]
        limits = controller.command_output_limits
        output = None if limits is None else [array(cb.to_torch(value)).reshape(-1).tolist() for value in limits]
        inputs = ControllerView.get_command_input_limits(key)
        inputs = None if inputs is None else [array(value).reshape(-1).tolist() for value in inputs]
        if name == "base":
            if (type(controller).__name__ != "HolonomicBaseJointController" or controller.motor_type != "velocity"
                    or inputs != [[-1., -1., -1.], [1., 1., 1.]]
                    or output != [[-.75, -.75, -1.], [.75, .75, 1.]]):
                raise ValueError("R1Pro base controller scaling changed")
            result[name] = {"input_limits": inputs, "output_limits": output}
            continue
        dofs = array(ControllerView.get_dof_idx(key)).astype(int)
        names = [robot.dof_names_ordered[index] for index in dofs]
        lower, upper = array(robot.joint_lower_limits)[dofs], array(robot.joint_upper_limits)[dofs]
        item = {"joint_names": names, "joint_limits": np.column_stack([lower, upper]).tolist()}
        if name.endswith("gripper"):
            if (type(controller).__name__ != "MultiFingerGripperController" or controller.control_type != ControlType.POSITION
                    or controller._mode != "smooth" or len(names) != 2 or inputs != [[-1.], [1.]]
                    or output is None or not np.allclose(lower, lower[0]) or not np.allclose(upper, upper[0])):
                raise ValueError("Expected symmetric smooth-position R1Pro fingers")
            item.update(unit="m", input_limits=inputs, output_limits=output, inverted=bool(controller._inverted))
        else:
            if (type(controller).__name__ != "JointController" or controller.motor_type != "position"
                    or controller.use_delta_commands or inputs is not None or output is not None):
                raise ValueError("Expected unnormalized absolute R1Pro joint targets")
            item["unit"] = "rad"
        if not np.isfinite(item["joint_limits"]).all():
            raise ValueError("R1Pro joint limits must be finite")
        result[name] = item
    return result


class NativeEpisode:
    def __init__(self, path, *, task, seed=0, instance_index=0, mode="public_test"):
        root = validate_source(path)
        import omnigibson as og
        import torch
        from omegaconf import OmegaConf
        from omnigibson.eval.evaluator import BatchedEvaluator, resolve_instance_ids
        from omnigibson.eval.utils.eval_utils import seed_everything
        from omnigibson.macros import gm

        if not Path(og.__file__).resolve().is_relative_to(root):
            raise ValueError("Imported OmniGibson differs from the pinned checkout")
        gm.HEADLESS = True
        gm.RENDER_VIEWER_CAMERA = False
        seed_everything(seed)
        instance = resolve_instance_ids(task, [instance_index], mode=mode)[0]

        class EnvironmentOnlyEvaluator(BatchedEvaluator):
            def load_policy(self):
                # The harness owns prediction and prefix execution; never start another policy client.
                return None

        cfg = OmegaConf.create(dict(env_wrapper={"_target_": "omnigibson.eval.wrappers.DefaultWrapper"},
            policy_name="local", model={}, headless=True, partial_scene_load=True, max_steps=None,
            write_video=False, mode=mode, seed=seed, num_envs=1, task={"name": task}, robot=None))
        self.og, self.torch, self.closed = og, torch, False
        self.evaluator = None
        try:
            self.evaluator = EnvironmentOnlyEvaluator(cfg)
            # Native load_batch resets controllers, restores the TRO, settles, resets once and resets metrics.
            self.evaluator.load_batch({0: instance}, write_video=False)
            self.state = self.evaluator.instance_eval_states[0]
            self.robot = self.state.env_accessor.robot
            self.controllers = controller_description(self.robot)
            if (not np.isclose(og.sim.get_rendering_dt(), CONTROL_DT)
                    or not np.isclose(og.sim.get_physics_dt(), 1 / 120)):
                raise ValueError("Expected native BEHAVIOR 30 Hz control and 120 Hz physics")
            snapshot = array(og.sim.dump_state(serialized=True))
            self.reset_sha256 = hashlib.sha256(snapshot.tobytes()).hexdigest()
            self.metadata = dict(task_id=task, seed=seed, instance_id=int(instance), instance_index=instance_index,
                evaluation_mode=mode, native_revision=REVISION, reset_sha256=self.reset_sha256,
                max_steps=self.evaluator.max_steps, grasping_mode="assisted", evaluation_regime="synchronous_hybrid")
            goals = self.evaluator.env.task.activity_natural_language_goal_conditions
            self.instruction = "Complete the task: " + task.replace("_", " ") + "."
            if goals:
                self.instruction += " Requirements: " + "; ".join(str(goal) for goal in goals)
        except BaseException:
            try:
                self.close()
            finally:
                self.shutdown()
            raise

    @property
    def observation(self):
        return self.state.obs

    @property
    def camera_names(self):
        return self.evaluator.robot_camera_names

    def advance(self, action):
        before = float(self.og.sim.current_time)
        actions = self.torch.as_tensor(action, dtype=self.torch.float32, device=self.og.sim.device)[None, :]
        terminated, truncated, _ = self.evaluator._apply_actions(actions, [0])
        duration = float(self.og.sim.current_time) - before
        if not np.isclose(duration, CONTROL_DT, atol=1e-6):
            raise RuntimeError("Native BEHAVIOR step duration changed")
        return bool(terminated[0]), bool(truncated[0]), duration

    def result(self):
        result = {"task_success": bool(self.state.env_accessor.success)}
        for metric in self.state.metrics:
            result.update(metric.aggregate())
        return json_metrics(result)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.evaluator is not None:
                self.evaluator.env.close()

    def shutdown(self):
        """Release the process-wide simulator after the HTTP host has stopped."""
        self.og.shutdown()
