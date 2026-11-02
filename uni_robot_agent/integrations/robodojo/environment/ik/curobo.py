"""cuRobo v2 boundary for RoboDojo ee67a146; no global solver monkeypatches.

The native planner owns the 32-seed solver. This wrapper owns only the lazy
128-seed fallback and serializes shared planner access across room adapters.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock

import numpy as np

_REGISTRY_LOCK = RLock()


@dataclass
class Candidate:
    joints: np.ndarray
    success: bool
    feasible: bool
    source: str
    seed_index: int
    position_error_m: float
    orientation_error_rad: float
    converged: bool


def _array(value):
    return value.detach().cpu().numpy()


class Curobo:
    def __init__(self, planner):
        self.planner = planner
        self.lock = RLock()
        self.users = 0
        self.extra = None

    def _activate_device(self):
        import torch
        torch.cuda.set_device(self.planner.device_cfg.device)

    def _use_cuda_graph(self):
        return self.planner.use_cuda_graph

    @classmethod
    def acquire(cls, planner):
        with _REGISTRY_LOCK:
            wrapper = getattr(planner, "_uni_robot_agent_ik", None)
            if wrapper is None:
                wrapper = cls(planner)
                planner._uni_robot_agent_ik = wrapper
            wrapper.users += 1
            return wrapper

    def release(self):
        with _REGISTRY_LOCK, self.lock:
            self.users -= 1
            if self.users == 0:
                if self.extra is not None:
                    self._activate_device()
                    self.extra.destroy()
                    self.extra = None
                del self.planner._uni_robot_agent_ik

    @contextmanager
    def session(self):
        with self.lock:
            self._activate_device()
            yield self

    def _solver(self, seeds):
        if seeds == 32:
            return self.planner.ik_solver
        if seeds != 128:
            raise ValueError("Only the native 32 and fallback 128 seed budgets are supported")
        if self.extra is None:
            from curobo.inverse_kinematics import InverseKinematics, InverseKinematicsCfg
            p = self.planner
            self.extra = InverseKinematics(InverseKinematicsCfg.create(
                robot=p.robot_cfg, scene_model=p.scene_model, device_cfg=p.device_cfg,
                num_seeds=128, seed_solver_num_seeds=128,
                position_tolerance=.001, orientation_tolerance=.02,
                self_collision_check=True, use_cuda_graph=self._use_cuda_graph(),
                max_batch_size=1, multi_env=False, max_goalset=1,
            ))
        return self.extra

    def _goal(self, native_pose, robot_origin):
        from utils.transformer import calculate_target_pose
        p = self.planner
        target = calculate_target_pose(robot_origin, p.robot_origin_pose, list(native_pose))
        position, orientation = p._trans_from_world_to_base(
            np.asarray(p.robot_origin_pose, np.float32), np.asarray(target, np.float32),
        )
        return p._build_goal_pose(position + p.frame_bias, orientation)

    def _candidates(self, result, label, solver):
        js = result.js_solution
        names = list(js.joint_names)
        indices = [names.index(n) for n in self.planner.active_joints_name]
        joints = _array(js.position).reshape(-1, len(names))[:, indices]
        success, feasible = _array(result.success).reshape(-1), _array(result.feasible).reshape(-1)
        seeds = _array(result.debug_info["seed_idx"]).reshape(-1)
        positions = _array(result.position_error).reshape(-1)
        orientations = _array(result.rotation_error).reshape(-1)
        return [Candidate(
            q.copy(), bool(ok), bool(valid), f"{label}:{i}", int(seed), float(pe), float(re),
            bool(pe < solver.config.position_tolerance and re < solver.config.orientation_tolerance),
        ) for i, (q, ok, valid, seed, pe, re) in enumerate(
            zip(joints, success, feasible, seeds, positions, orientations))]

    def solve(self, native_pose, current, robot_origin, *, seeds=32):
        """Capture top1 and all candidates from the same optimizer invocation."""
        solver = self._solver(seeds)
        original = solver._get_result
        previous_override = solver.__dict__.get("_get_result")
        top1 = []

        def capture(opt, metrics, num_seeds, return_seeds):
            # top-k ties need not preserve the native k=1 winner when k grows.
            one = original(opt, metrics, num_seeds, 1)
            top1[:] = self._candidates(one, f"{seeds}/top1", solver)
            return original(opt, metrics, num_seeds, return_seeds)

        solver._get_result = capture
        try:
            result = solver.solve_pose(
                goal_tool_poses=self._goal(native_pose, robot_origin),
                current_state=self.planner._build_joint_state(current), return_seeds=seeds,
            )
        finally:
            # Restore even on CUDA/solver exceptions; never leave a room's hook installed.
            if previous_override is None:
                del solver._get_result
            else:
                solver._get_result = previous_override
        # seed_idx identifies the optimizer output, independent of top-k ordering.
        # Keep the true top1 first; equal joint vectors from different seeds remain distinct.
        unique = {}
        for candidate in top1 + self._candidates(result, f"{seeds}/rank", solver):
            unique.setdefault(candidate.seed_index, candidate)
        return list(unique.values())

    def constraints(self, joints):
        """Check exact fixed q, without IK optimization, using native constraints.

        Call after solve(32) in the same session. Pad chunks to the existing
        32-seed rollout shape so validation neither grows CUDA graphs nor builds
        a second collision model. Tool convergence is deliberately not used.
        """
        import torch
        rows = np.atleast_2d(np.asarray(joints, float))
        feasible, costs = [], []
        p = self.planner
        for offset in range(0, len(rows), 32):
            chunk = rows[offset:offset + 32]
            full = np.stack([p._build_cspace_joint_values(q) for q in chunk])
            full = np.concatenate([full, np.repeat(full[-1:], 32 - len(full), axis=0)])
            action = torch.as_tensor(full, device=p.device_cfg.device, dtype=torch.float32)[:, None, :]
            metrics = p.ik_solver.metrics_rollout.compute_metrics_from_action(action)
            native = metrics.costs_and_constraints
            ok = native.get_feasible(include_all_hybrid=False, sum_horizon=True)
            valid = np.full(32, ok, dtype=bool) if isinstance(ok, bool) else _array(ok).reshape(32, -1).all(axis=1)
            feasible.extend(valid[:len(chunk)].tolist())
            values = [_array(v).reshape(32, -1).sum(axis=1) for v in native.constraints.values]
            cost = np.stack(values, axis=1) if values else np.zeros((32, 1))
            costs.extend(cost[:len(chunk)].tolist())
        return np.asarray(feasible, bool), np.asarray(costs, float)


def acquire_solvers(env):
    """Borrow official native planners on the environment owner thread."""
    manager = env.robot_manager
    return {robot.arm_name.removesuffix("_arm"): Curobo.acquire(manager.ik_solver[robot.robot_name])
            for robot in manager.robot_list if robot.type == "target"}
