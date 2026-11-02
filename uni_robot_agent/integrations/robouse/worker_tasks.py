"""Explicit worker task admission, pinned to RoboUse 0.4.1 task registries."""
from pathlib import Path

GPU_TASKS = {'maniskill': {'peg-insertion-side': 100,
               'peg-insertion-side-1mm': 100,
               'peg-insertion-moving-box': 120,
               'peg-insertion-side-vision': 150,
               'plug-charger': 40,
               'plug-charger-vision': 60,
               'stack-pyramid': 40,
               'pull-cube-tool': 40,
               'pick-moving-cube': 150,
               'stack-pyramid-vision': 60,
               'pick-moving-cube-vision': 150,
               'lift-peg-upright': 40},
 'genesis': {'pour-water': 60, 'pour-water-narrow': 60, 'pour-water-vision': 80},
 'isaaclab': {'factory-peg-insert': 300,
              'factory-gear-mesh': 400,
              'factory-nut-thread': 600,
              'factory-peg-insert-noisy': 300,
              'factory-nut-thread-noisy': 600}}


def family(spec):
    if spec["backend"] == "maniskill":
        return "maniskill_cpu"
    if spec["backend"] == "robocasa":
        return "robocasa"
    if spec["backend"] == "gpu":
        return {"maniskill": "maniskill_gpu", "genesis": "genesis", "isaaclab": "factory"}[spec["sim"]]
    return None


def validate(spec):
    kind = spec["backend"]
    if kind == "gpu":
        tasks = GPU_TASKS.get(spec.get("sim"), {})
        if spec.get("env") not in tasks:
            raise ValueError("Only pinned ManiSkill, Genesis and Factory GPU tasks are admitted")
        if spec.get("opts"):
            raise ValueError("Custom GPU task options require a separate compatibility review")
    else:
        if kind == "maniskill":
            from robouse.backends.maniskill.backend import TASKS
        else:
            from robouse.backends.robocasa.backend import TASKS
        if (spec.get("env") or spec.get("id")) not in TASKS:
            raise ValueError("Unknown pinned worker task")


def bridge_spec(spec, python, cameras, work_dir):
    validate(spec)
    if spec["backend"] == "gpu":
        budget = GPU_TASKS[spec["sim"]][spec["env"]]
    else:
        if spec["backend"] == "maniskill":
            from robouse.backends.maniskill.backend import TASKS
        else:
            from robouse.backends.robocasa.backend import TASKS
        budget = TASKS[spec.get("env") or spec["id"]]["max_steps"]
    if python is None or not Path(python).is_file():
        raise ValueError("Supply --worker-python pointing to the prepared simulator interpreter")
    return {**spec, "backend": "uni_robot_agent_worker", "max_steps": spec.get("max_steps", budget),
            "uni_robot_agent_original_backend": spec["backend"], "uni_robot_agent_family": family(spec),
            "uni_robot_agent_worker_python": str(Path(python).absolute()), "uni_robot_agent_cameras": cameras,
            "uni_robot_agent_work_dir": str(Path(work_dir).resolve())}
