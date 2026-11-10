"""Public navigation context; native goals and metrics are never prompts."""
from pathlib import Path

from uni_robot_agent.context.prompt import Prompt, register_prompt_builder


@register_prompt_builder("navila")
def build(context):
    episode = context.episode
    text = (Path(__file__).resolve().parents[2] / "resources/prompts/navila/hybrid.md").read_text()
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=text)
    if episode:
        prompt = prompt.append(system="Environment description:\n" + episode.description.environment.model_dump_json(),
                               user="Initial measured state:\n" + episode.state.model_dump_json())
    return prompt
