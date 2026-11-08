"""Compose DexJoCo capabilities over the shared prepared episode lifecycle."""
from uni_robot_agent.integrations.hybrid import configure_hybrid
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.services.environment.device_contracts import DeviceEpisodeInfo
from uni_robot_agent.tools.episode import EpisodeTools
from uni_robot_agent.tools.scene.provider import SceneTools
from uni_robot_agent.tools.skills import SkillTools


def configure(session):
    from .action_binding import create_binding
    info = session.episode.description
    if not isinstance(info, DeviceEpisodeInfo) or info.integration != "dexjoco":
        raise ValueError("DexJoCo requires a prepared dexjoco device environment")
    if session.args.action_profile != "hybrid":
        raise ValueError("DexJoCo requires a configured hybrid actor")
    hybrid = configure_hybrid(session, binding_factory=create_binding)
    return (SkillTools(session.episode, writing=getattr(session.args, "skill_writing", False)),
            SceneTools(session.episode), hybrid, EpisodeTools(session.episode))


def create_session(context):
    return PreparedEpisodeSession(context, configure_tools=configure)
