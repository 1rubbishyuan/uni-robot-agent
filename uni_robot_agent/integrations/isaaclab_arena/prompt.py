"""Measured whole-body context; tools own their schemas."""
from uni_robot_agent.context.prompt import Prompt, register_prompt_builder


@register_prompt_builder("isaaclab_arena")
def build(context):
    episode = context.episode
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the described humanoid through actor_act and inspect fresh camera observations. "
        "The robot coordinates arms, hands, walking and pelvis height. Native whole-body control owns "
        "balance, legs and waist; they are not independent direct controls. "
        "Previews describe requested controller references, not predicted trajectories or contact. "
        "A completed action does not establish task success. The native evaluator owns the outcome. "
        "Do not inspect simulator implementation, privileged object poses or task predicates."
    ))
    if episode:
        prompt = prompt.append(system="Environment description:\n" + episode.description.environment.model_dump_json(),
                               user="Initial measured state:\n" + episode.state.model_dump_json())
    return prompt
