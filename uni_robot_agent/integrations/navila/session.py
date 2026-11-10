"""Compose a prepared navigation episode with shared Hybrid services."""
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.services.environment.device_contracts import DeviceEpisodeInfo


def validate_episode(session):
    info = session.episode.description
    if not isinstance(info, DeviceEpisodeInfo) or info.integration != "navila":
        raise ValueError("NaVILA requires a prepared NaVILA device episode")


def create_session(context):
    from .action_binding import create_binding
    return PreparedEpisodeSession(context, binding_factory=create_binding, validate_episode=validate_episode)
