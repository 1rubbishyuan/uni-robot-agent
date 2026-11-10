"""Supply task identity and measured environment context."""
from uni_robot_agent.context.prompt import Prompt, register_prompt_builder


@register_prompt_builder("behavior")
def build(context):
    episode = context.episode
    result = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the described mobile dual-arm robot using the registered tools. "
        "Inspect fresh RGB and robot measurements to judge action effects. "
        "Do not inspect simulator source, task predicates, hidden object poses or evaluator state. "
        "Only the native task evaluator determines the outcome after finish."
    ))
    if episode:
        result = result.append(system="Environment description:\n" + episode.description.environment.model_dump_json(),
                               user="Initial measured state:\n" + episode.state.model_dump_json())
    return result
