"""Conservative task admission for the pinned RoboUse release."""
import json
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

SUPPORTED = ("tabletop", "metaworld", "gymrobotics", "kitchen", "libero", "robosuite", "menagerie", "components", "maniskill", "robocasa", "gpu")


def check_version():
    if version("robouse") != "0.4.1":
        raise ValueError("This integration requires robouse==0.4.1")


def validate_spec(spec):
    kind = spec.get("backend")
    if kind not in SUPPORTED:
        raise ValueError(f"Backend {kind!r} is not admitted; supported: {SUPPORTED}")
    if kind in {"maniskill", "robocasa", "gpu"}:
        from .worker_tasks import validate
        validate(spec)
    if kind == "gymrobotics" and not str(spec.get("env", "")).startswith("Fetch"):
        raise ValueError("Only fixed-base Fetch tasks are admitted")
    if kind == "tabletop":
        scenario = spec.get("scenario")
        if not scenario:
            from robouse.backends.tabletop.scenarios import SCENARIOS
            scenario = SCENARIOS[spec["id"]]
        if scenario.get("image_examples"):
            raise ValueError("Reference-image tasks are deferred until task assets are published")
        if spec.get("safety", scenario.get("safety")) == "refuse":
            raise ValueError("Refusal-scored tasks are deferred")
    if kind == "components":
        from robouse.backends.components.backend import read_block
        from robouse.backends.components.sim.templates import get
        block = read_block(spec)
        if block["embodiment"] not in {"franka-panda", "aloha-2"}:
            raise ValueError("Only fixed Panda/ALOHA composed tasks are admitted")
        if block["modifiers"].get("safety", "none") != "none" or callable(getattr(get(block["task"]), "judge_text", None)):
            raise ValueError("Composed safety and text-submission tasks are deferred")
    if kind == "robosuite":
        robots = spec.get("robots", "Panda")
        robots = [robots] if isinstance(robots, str) else robots
        if any(robot != "Panda" for robot in robots) or spec.get("env") in {"Wipe", "TwoArmPegInHole"}:
            raise ValueError("Only Panda parallel-gripper robosuite tasks are admitted")
    if kind == "menagerie":
        from robouse.backends.menagerie.backend import TASKS
        task = TASKS[spec.get("env") or spec["id"]]
        if task["arms"] == "switch":
            raise ValueError("Arm-switch tasks are deferred: atomic two-arm commands are unavailable")


def load(path):
    check_version()
    from robouse.tasks import load_task
    from robouse.core.prompts import ROBO_USAGE, EMBODIED_USAGE
    task = load_task(path)
    validate_spec(task.spec)
    instruction = task.instruction
    # Remove only the exact upstream control boilerplate, retaining task notes after it.
    for usage in (ROBO_USAGE, EMBODIED_USAGE):
        instruction = instruction.replace(usage, "")
    if task.spec["backend"] == "tabletop":
        scenario = task.spec.get("scenario")
        if not scenario:
            from robouse.backends.tabletop.scenarios import SCENARIOS
            scenario = SCENARIOS[task.spec["id"]]
        # Demonstrations are public task inputs, independent of live scene truth.
        examples = (scenario.get("public") or {}).get("examples")
        if examples:
            instruction += "\n\n## Task demonstrations\n\n" + json.dumps({"examples": examples})
    digest = sha256((Path(path) / "task.md").read_bytes()).hexdigest()
    return task, instruction.strip(), digest
