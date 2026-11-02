"""DROID-specific context; shared providers own all tool instructions."""
from uni_robot_agent.context.prompt import Prompt, register_prompt_builder


@register_prompt_builder("robolab")
def build(context):
    episode = context.episode
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control one DROID robot in RoboLab using the registered tools. "
        "The registered control site is the Robotiq base_link mounting flange, not the fingertip midpoint "
        "or the rotated eef_frame. Public poses use world metres; move orientations use fixed-axis XYZ radians. "
        "Follow the shared tool schema for jaw opening units. The adapter converts physical jaw gaps through "
        "the nonlinear Robotiq linkage, bounded by native travel. Measured opening and drive intent differ "
        "when an object blocks the fingers; closing or reaching a pose does not prove a grasp. "
        "Use fresh sensor observations to verify effects. Cameras provide RGB and calibrated optical poses. "
        "Depth is unavailable; use matched features across cameras when triangulation is offered. "
        "Robot calibration describes conservative robot-only collision hulls; it does not cover scene obstacles. "
        "Native RoboLab results determine task success. A finish claim does not set that result. "
        "Do not inspect simulator internals, task predicate code, or privileged object recordings."
    ))
    if episode:
        prompt = prompt.append(system="Environment description:\n" + episode.description.environment.model_dump_json(),
                               user="Initial measured state:\n" + episode.state.model_dump_json())
    return prompt
