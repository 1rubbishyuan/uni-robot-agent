"""Embodiment-independent tool declarations for prepared episodes."""
from uni_robot_agent.integrations.composition import ToolModule
from uni_robot_agent.tools import episode, skills
from uni_robot_agent.tools.episode import EpisodeTools
from uni_robot_agent.tools.scene import provider as scene
from uni_robot_agent.tools.scene.provider import SceneTools
from uni_robot_agent.tools.skills import SkillTools


def configure_scene(session):
    perception = None
    if session.args.sam3_url:
        from uni_robot_agent.services.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(session.args.sam3_url, tracer=session.context.tracer))
    return SceneTools(session.episode, perception=perception)


SKILLS_MODULE = ToolModule("skills",
    lambda session: SkillTools(session.episode, writing=session.args.skill_writing),
    skills.tool_policies())
SCENE_MODULE = ToolModule("scene", configure_scene, scene.tool_policies())
EPISODE_MODULE = ToolModule("episode", lambda session: EpisodeTools(session.episode), episode.tool_policies())

