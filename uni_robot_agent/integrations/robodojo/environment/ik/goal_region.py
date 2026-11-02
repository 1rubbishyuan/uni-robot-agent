"""Bounded search about the original final grasp-site goal; no physical steps."""
from time import monotonic

import numpy as np
from scipy.optimize import least_squares, minimize
from scipy.spatial.transform import Rotation

from ..kinematics import native_pose, pose, transform
from .recovery import equivalent, pose_errors

SEARCH_MARGIN = .8
MAX_STARTS = 4
MAX_ITERATIONS = 90
SEARCH_TIMEOUT_S = 10.


class SearchBudgetExceeded(Exception):
    pass


def search(recovery, site_fk, current, goal, position_tolerance, orientation_tolerance):
    """Return a pose to feed to the existing waypoint loop, never executable q."""
    current = np.asarray(current, float)
    target = transform(goal.position, goal.quaternion)
    pt, rt = position_tolerance * SEARCH_MARGIN, orientation_tolerance * SEARCH_MARGIN
    deadline = monotonic() + SEARCH_TIMEOUT_S
    low, high = recovery.limits.T
    counts = {"optimizer_starts": 0, "constraint_evaluations": 0,
              "solver_calls": 0, "seeds_attempted": 0, "candidates_returned": 0}

    def check_budget():
        if monotonic() >= deadline:
            raise SearchBudgetExceeded

    def residual(q):
        check_budget()
        actual = recovery.root @ site_fk.matrix(q)
        return np.r_[(actual[:3, 3] - target[:3, 3]) / pt,
                     Rotation.from_matrix(actual[:3, :3] @ target[:3, :3].T).as_rotvec() / rt]

    cached_q, cached_cost = None, None

    def constraints(q):
        nonlocal cached_q, cached_cost
        errors = residual(q)
        if cached_q is None or not np.array_equal(q, cached_q):
            _, costs = recovery.solver.constraints([q])
            cached_q, cached_cost = q.copy(), costs[0]
            counts["constraint_evaluations"] += 1
        return np.r_[1 - errors[:3] @ errors[:3], 1 - errors[3:] @ errors[3:], -cached_cost]

    def accepted(q, source):
        check_budget()
        if not np.isfinite(q).all() or np.any(q < low) or np.any(q > high):
            return None
        actual = recovery.root @ site_fk.matrix(q)
        pe, re = pose_errors(actual, target)
        # Exact independent geometry check: cost-space convergence is not a radius.
        if pe > pt or re > rt:
            return None
        valid, _ = recovery.solver.constraints([q])
        if not valid.all():
            return None
        result = pose(actual)
        return {"found": True, "pose": {"position": result["position"],
                                         "quaternion": result["quaternion_wxyz"]},
                "diagnostics": {"source": source, "search_margin": SEARCH_MARGIN,
                                "position_error_m": pe, "orientation_error_rad": re,
                                "physical_steps": 0, **counts}}

    try:
        with recovery.solver.session():
            candidates = recovery.solver.solve(
                native_pose(goal.position, goal.quaternion), current, recovery.robot_origin, seeds=32,
            )
            # Region queries have their own denominator, separate from strict waypoint IK.
            counts.update(solver_calls=1, seeds_attempted=32, candidates_returned=len(candidates))
            legal = [q for item in candidates
                     if (q := equivalent(item.joints, current, recovery.limits)) is not None]
            legal.sort(key=lambda q: np.linalg.norm(q - current))
            for q in legal:
                result = accepted(q, "existing_candidate")
                if result is not None:
                    return result
            seeds = [np.clip(current, low, high)]
            fit = least_squares(residual, seeds[0], bounds=(low, high), max_nfev=80,
                                ftol=1e-9, xtol=1e-9, gtol=1e-9)
            seeds.insert(0, fit.x)
            for q in legal:
                if len(seeds) >= MAX_STARTS:
                    break
                if all(np.linalg.norm(q - previous) > .1 for previous in seeds):
                    seeds.append(q)
            for seed in seeds:
                check_budget()
                counts["optimizer_starts"] += 1
                fit = minimize(lambda q: .5 * np.sum((q - current) ** 2), seed,
                               jac=lambda q: q - current, method="SLSQP", bounds=list(zip(low, high)),
                               constraints={"type": "ineq", "fun": constraints},
                               options={"maxiter": MAX_ITERATIONS, "ftol": 1e-9, "eps": 1e-4})
                result = accepted(fit.x, "constrained_region_search")
                if result is not None:
                    return result
    except SearchBudgetExceeded:
        return {"found": False, "diagnostics": {"reason": "search_budget", "physical_steps": 0, **counts}}
    return {"found": False, "diagnostics": {"reason": "no_feasible_goal", "physical_steps": 0, **counts}}
