"""Explicit per-backend control bindings; imports do not initialize simulators."""

def create(backend, kind):
    if getattr(backend, "uni_robot_agent_worker", False):
        from .worker_control import WorkerControl
        return WorkerControl(backend)
    if kind in {"tabletop", "metaworld", "gymrobotics", "kitchen"}:
        from .cartesian import Cartesian
        return Cartesian(backend, kind)
    if kind in {"libero", "robosuite"}:
        from .osc import OSC
        return OSC(backend, kind)
    if kind in {"menagerie", "components"}:
        from .bimanual import Bimanual
        return Bimanual(backend, kind)
    raise ValueError(f"Unregistered RoboUse backend: {kind}")
