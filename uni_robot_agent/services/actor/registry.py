"""Actor configuration and lifecycle entry points without native imports."""
from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class BackendDefinition:
    name: str
    package: str
    backend_class: str
    deployment_module: str | None = None

    def resolve_config(self, settings):
        return import_module(f"{self.package}.config").resolve_config(settings)

    def service_limits(self, config):
        return import_module(f"{self.package}.config").service_limits(config)

    def create(self, config):
        factory = getattr(import_module(f"{self.package}.backend"), self.backend_class)
        return factory(config)

    def deployment(self):
        if self.deployment_module is None:
            raise ValueError(f"Actor backend {self.name!r} has no managed deployment; launch its service directly")
        return import_module(self.deployment_module)


_PACKAGE = "uni_robot_agent.services.actor.backends"
_BACKENDS = {
    "openpi": BackendDefinition("openpi", f"{_PACKAGE}.openpi", "OpenpiBackend",
                                f"{_PACKAGE}.openpi.deployment"),
    "xpolicylab": BackendDefinition("xpolicylab", f"{_PACKAGE}.xpolicylab", "XPolicyLabBackend"),
}
_ALIASES = {"pi05": "openpi"}


def backend_names():
    return (*_BACKENDS, *_ALIASES)


def get_backend(name):
    canonical = _ALIASES.get(name, name)
    try:
        return _BACKENDS[canonical]
    except KeyError:
        raise ValueError(f"Unsupported actor backend: {name}") from None
