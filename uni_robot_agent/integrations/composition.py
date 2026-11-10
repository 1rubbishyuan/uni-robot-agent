"""Explicit integration tool modules; execution modes remain owned by the executor."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from uni_robot_agent.execution.scheduler import ToolExecutionPolicy
from uni_robot_agent.tools.toolkit import ToolDef, ToolProvider


@dataclass(frozen=True)
class ToolModule:
    """One provider factory with its policies and optional debug-only declarations."""

    name: str
    create: Callable[[Any], ToolProvider]
    policies: Mapping[str, ToolExecutionPolicy] = field(default_factory=dict)
    runtime_prompts: tuple[str, ...] = ()
    declare_unavailable: Callable[[Any], list[ToolDef]] | None = None
    model_view: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    prompt_contributions: tuple | None = None


@dataclass(frozen=True)
class ToolAssembly:
    """The selected modules, instantiated once against one prepared episode."""

    modules: tuple[ToolModule, ...]
    providers: tuple[ToolProvider, ...]

    @property
    def tools(self) -> list[ToolDef]:
        return [replace(tool, model_view=module.model_view) if module.model_view is not None else tool
                for module, provider in zip(self.modules, self.providers)
                for tool in provider.get_tools()]

    @property
    def prompt_contributions(self):
        return tuple(contribution for module, provider in zip(self.modules, self.providers)
                     for contribution in (module.prompt_contributions if module.prompt_contributions is not None
                                          else provider.prompt_contributions))

    @property
    def policies(self) -> dict[str, ToolExecutionPolicy]:
        return {name: policy for module in self.modules for name, policy in module.policies.items()}

    @property
    def runtime_prompts(self) -> tuple[str, ...]:
        return tuple(path for module in self.modules for path in module.runtime_prompts)

    def debug_catalog(self, session: Any, modules: tuple[ToolModule, ...]):
        """Declare unavailable features without starting their services."""
        tools = [tool for provider in self.providers for tool in provider.get_declared_tools()]
        policies = self.policies
        active = {module.name for module in self.modules}
        for module in modules:
            if module.name not in active and module.declare_unavailable is not None:
                tools.extend(module.declare_unavailable(session))
                policies.update(module.policies)
        return tools, policies


def validate_modules(modules: tuple[ToolModule, ...]) -> None:
    names, policies = set(), set()
    for module in modules:
        if module.name in names:
            raise ValueError(f"Duplicate tool module: {module.name}")
        names.add(module.name)
        if overlap := policies.intersection(module.policies):
            raise ValueError(f"Duplicate tool policies: {sorted(overlap)}")
        policies.update(module.policies)


def assemble_tools(session: Any, modules: tuple[ToolModule, ...]) -> ToolAssembly:
    validate_modules(modules)
    return ToolAssembly(modules, tuple(module.create(session) for module in modules))


def resolve_tool_profile(args: Any, spec: Any) -> None:
    """Resolve the new selector and legacy alias before opening any services."""
    requested = getattr(args, "tool_profile", None)
    legacy = getattr(args, "action_profile", None)
    if requested is not None and legacy is not None and requested != legacy:
        raise ValueError("--tool-profile and --action-profile select different tool profiles")
    selected = requested or legacy or spec.default_tool_profile
    if selected not in spec.tool_profiles:
        raise ValueError(f"Integration {spec.name!r} does not support tool profile {selected!r}; "
                         f"choose from {', '.join(spec.tool_profiles)}")
    modules = spec.tool_profiles[selected]
    validate_modules(modules)
    args.tool_profile = selected
    args.tool_modules = tuple(module.name for module in modules)
    # Keep existing result consumers working; runtime decisions use resolved modules.
    args.action_profile = "hybrid" if "hybrid" in args.tool_modules else "direct"
