"""uni-robot-agent — A robot agent built on Codex and Claude Code."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("uni-robot-agent")
except PackageNotFoundError:
    __version__ = "unknown (not installed)"


# Keep version/help and record readers independent of planner and native runtimes.
_EXPORTS = {
    "SessionConfig": ("config", "SessionConfig"),
    "AgentConfig": ("config", "AgentConfig"),
    "Session": ("session", "Session"),
    "run": ("session", "run_session"),
    "ToolClient": ("tools.client", "ToolClient"),
    "ToolJob": ("tools.client", "ToolJob"),
    "ToolOutput": ("tools.toolkit", "ToolOutput"),
    "RunResult": ("artifacts.records", "RunResult"),
    "RunRecord": ("artifacts.records", "RunRecord"),
    "SetupConfig": ("services.deployment.config", "SetupConfig"),
    "DeploymentConfig": ("services.deployment.config", "DeploymentConfig"),
    "Deployment": ("services.deployment.manager", "Deployment"),
    "setup": ("services.deployment.setup", "setup"),
    "deploy": ("services.deployment.manager", "start_deployment"),
}
__all__ = ["__version__", *_EXPORTS]


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(f"{__name__}.{module}"), attribute)
    globals()[name] = value
    return value
