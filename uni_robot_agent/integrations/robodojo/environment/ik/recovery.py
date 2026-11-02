"""Strict waypoint IK recovery; this module never advances physics."""
from collections import Counter

import numpy as np
from scipy.spatial.transform import Rotation

from ..kinematics import transform
from ..native_compat import MAX_IK_JOINT_DELTA_RAD

POSITION_TOLERANCE_M = .001
ORIENTATION_TOLERANCE_RAD = .02
CONNECTION_SAMPLE_RAD = .025


def equivalent(joints, current, limits):
    """Return actual legal angles, not a wrapped delta hiding a large command."""
    joints, current, limits = map(lambda x: np.asarray(x, float), (joints, current, limits))
    if joints.shape != current.shape or not np.isfinite(joints).all():
        return None
    low = np.ceil((limits[:, 0] - joints) / (2 * np.pi))
    high = np.floor((limits[:, 1] - joints) / (2 * np.pi))
    if np.any(low > high):
        return None
    turns = np.clip(np.rint((current - joints) / (2 * np.pi)), low, high)
    return joints + 2 * np.pi * turns


def pose_errors(actual, goal):
    return (float(np.linalg.norm(actual[:3, 3] - goal[:3, 3])),
            float(Rotation.from_matrix(actual[:3, :3] @ goal[:3, :3].T).magnitude()))


class Recovery:
    def __init__(self, solver, native_fk, limits, root, robot_origin, *, guard=True):
        self.solver, self.native_fk = solver, native_fk
        self.limits = np.asarray(limits, float)
        self.root, self.robot_origin, self.guard = root, robot_origin, guard

    def _check(self, q, current, goal):
        if np.any(q < self.limits[:, 0]) or np.any(q > self.limits[:, 1]):
            return "joint_limits", {}
        pe, re = pose_errors(self.root @ self.native_fk.matrix(q), goal)
        if pe > POSITION_TOLERANCE_M or re > ORIENTATION_TOLERANCE_RAD:
            return "pose_error", {}
        delta = float(np.max(np.abs(q - current)))
        if self.guard and delta > MAX_IK_JOINT_DELTA_RAD:
            return "joint_jump_rejected", {}
        n = max(1, int(np.ceil(delta / CONNECTION_SAMPLE_RAD)))
        valid, _ = self.solver.constraints(np.linspace(current, q, n + 1))
        if not valid.all():
            return "collision_or_native_constraint", {}
        return None, {"connection_samples": n + 1, "native_position_error_m": pe,
                      "native_orientation_error_rad": re}

    def _candidate(self, candidate, current, goal, cache):
        reasons = set()
        if not candidate.converged:
            reasons.add("native_not_converged")
        if not candidate.feasible:
            reasons.add("native_constraint_rejected")
        if not candidate.success and not reasons:
            reasons.add("native_solver_rejected")
        if reasons:
            return None, {}, reasons
        raw = np.asarray(candidate.joints, float)
        if raw.shape != current.shape or not np.isfinite(raw).all():
            return None, {}, {"invalid_joint_target"}
        legal = equivalent(raw, current, self.limits)
        # Try the actual raw target first, then its legal whole-turn representation.
        for converted, q in ((False, raw), (True, legal)):
            if q is None:
                continue
            key = q.tobytes()
            if key not in cache:
                cache[key] = self._check(q, current, goal)
            reason, details = cache[key]
            if reason is None:
                return q, {"candidate_source": candidate.source, "seed_index": candidate.seed_index,
                           "equivalent_turns": converted, **details}, set()
            reasons.add(reason)
        return None, {}, reasons

    def solve(self, target, current):
        current = np.asarray(current, float)
        goal = transform(target[:3], target[3:])
        rejected, cache = Counter(), {}
        stats = {"ik_requests": 1, "ik_request_failures": 0, "solver_calls": 0,
                 "seeds_attempted": 0, "candidates_returned": 0,
                 "candidates_checked": 0, "candidates_rejected": 0}
        raw = None
        q, details = None, {}
        with self.solver.session():
            for budget in (32, 128):
                candidates = self.solver.solve(target, current, self.robot_origin, seeds=budget)
                stats["solver_calls"] += 1
                stats["seeds_attempted"] += budget
                stats["candidates_returned"] += len(candidates)
                if raw is None:
                    raw = candidates[0]
                # Each batch contains unique seed IDs. Do not rescan the earlier batch.
                for candidate in candidates:
                    stats["candidates_checked"] += 1
                    q, details, reasons = self._candidate(candidate, current, goal, cache)
                    if q is not None:
                        break
                    stats["candidates_rejected"] += 1
                    # One candidate may fail different stages in its raw/equivalent forms.
                    # Each stage counts that candidate at most once, only if finally rejected.
                    rejected.update(reasons)
                if q is not None:
                    break
        stats["ik_request_failures"] = int(q is None)
        raw_delta = (float(np.max(np.abs(raw.joints - current)))
                     if raw.joints.shape == current.shape and np.isfinite(raw.joints).all() else None)
        diagnostic = {
            "seed_budget": budget, "raw_top1_success": raw.success,
            "raw_top1_feasible": raw.feasible, "raw_top1_converged": raw.converged,
            "raw_top1_position_error_m": raw.position_error_m if np.isfinite(raw.position_error_m) else None,
            "raw_top1_orientation_error_rad": raw.orientation_error_rad if np.isfinite(raw.orientation_error_rad) else None,
            "raw_top1_max_joint_delta_rad": raw_delta,
            "rejected": dict(sorted(rejected.items())), "stats": stats, **details,
        }
        if q is not None:
            return {"status": "Success", "joint_value": q, "recovery": diagnostic}
        stages = set(rejected)
        native_stages = {"native_not_converged", "native_constraint_rejected", "native_solver_rejected"}
        reason = ("ik_failed" if stages <= native_stages else
                  "joint_jump_rejected" if stages == {"joint_jump_rejected"} else "ik_candidate_rejected")
        return {"status": "Fail", "reason": reason, "recovery": diagnostic}
