"""Agent contracts and lazy backend registration."""

from .contracts import Agent, AgentCapabilities, AgentResult
from .registry import (
    create_agent,
    get_agent_backend,
    list_agent_backends,
    register_agent,
)

__all__ = [
    "Agent",
    "AgentCapabilities",
    "AgentResult",
    "create_agent",
    "get_agent_backend",
    "list_agent_backends",
    "register_agent",
]
