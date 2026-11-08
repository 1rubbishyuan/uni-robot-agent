"""Task instructions without upstream shell tools or privileged scene state."""
from uni_robot_agent.context.prompt import Prompt, register_prompt_builder


@register_prompt_builder("robouse")
def build(context):
    episode = context.episode
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the robot only through the registered tools. Do not inspect source code, "
        "processes, files, or simulator internals, and do not use shell commands. "
        "Positions are world metres; quaternions are wxyz at the registered grasp site. "
        "Respect the environment's rotation constraints and verify effects in current observations. "
        "The environment judges the physical outcome; a completion claim does not override it. "
        "The task text preserves upstream benchmark notes. Any robo CLI examples or native action "
        "numbers there are reference documentation, not available tools: never execute them. "
        "Registered tool schemas override those control conventions. Upstream descriptions of object "
        "state fields do not grant access to them; this profile exposes images and robot proprioception, with depth only when configured."
    ))
    if episode:
        prompt = prompt.append(system="Environment description:\n" + episode.description.environment.model_dump_json(),
                               user=None if context.tool_profile == "minimal" else "Initial measured state:\n" + episode.state.model_dump_json())
    return prompt
