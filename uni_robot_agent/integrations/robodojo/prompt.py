"""RoboDojo instructions and plain-language task supplements."""

import json
from pathlib import Path

from uni_robot_agent.context.prompt import (
    Prompt, PromptContext, get_prompts_dir, register_prompt_builder, render_prompt_files,
)

REQUIREMENTS_PATH = (
    Path(__file__).resolve().parents[2] / "resources/prompts/robodojo/instruction_supplements.json"
)


def load_task_requirements(task: str) -> str:
    """Render the reviewed supplement for an exact native task name."""
    data = json.loads(REQUIREMENTS_PATH.read_text())
    if task not in data["tasks"]:
        raise ValueError(f"No reviewed instruction supplements for RoboDojo task: {task}")
    rules = data["tasks"][task]
    if not rules:
        return ""
    body = "\n".join(f"- {rule}" for rule in rules)
    return (
        f"## Additional task requirements: {task}\n\n"
        "These clarify the task instruction, including the required sequence.\n\n"
        f"{body}"
    )


@register_prompt_builder("robodojo")
def build(context: PromptContext) -> Prompt:
    episode = context.episode
    info = episode.description if episode is not None else None
    prompt = render_prompt_files(get_prompts_dir() / "robodojo", variables={
        "task": info.task if info is not None else context.task,
        "instruction": context.task or (episode.instruction if episode is not None else ""),
    })
    if episode is None:
        return prompt.append(user="No prepared episode is connected. Measured state and calibration are unavailable.")
    prompt = prompt.append(
        system="Environment description:\n" + info.environment.model_dump_json(indent=2),
        user=(f"Initial measured environment state: {episode.state.model_dump_json()}\n"
              f"Native remaining control steps: {episode.remaining_steps}"),
    )
    if info.environment.cameras and not any(camera.has_calibration for camera in info.environment.cameras):
        prompt = prompt.append(system=(
            "Camera calibration is disabled for this episode. Observations provide RGB without depth, "
            "camera intrinsics or camera extrinsics. locate, when exposed, provides 2D segmentation only. "
            "Capturing again does not enable metric localization. Use visible relations and bounded "
            "motions with feedback; keep inferred coordinates distinct from measurements. "
            "Robot proprioception and the published robot geometry remain available."
        ))
    if episode.task_requirements:
        prompt = prompt.append(user=episode.task_requirements)
    return prompt
