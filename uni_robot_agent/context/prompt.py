"""Environment prompt builders and shared runtime assembly."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    """Initial system/user input for one agent session."""

    user: str
    system: str | None = None
    sources: tuple[str, ...] = ()

    def flatten(self) -> str:
        """Flatten input for agents accepting a single string."""
        if self.system is None:
            return self.user
        return f"{self.system.rstrip()}\n\n{self.user.lstrip()}"

    def append(
        self,
        *,
        system: str | None = None,
        user: str | None = None,
        source: str | None = None,
    ) -> Prompt:
        """Append a section with consistent paragraph spacing and source tracking."""
        next_system = self.system
        if system:
            next_system = _append_text(next_system, system)
        next_user = self.user
        if user:
            next_user = _append_text(next_user, user)
        sources = self.sources + ((source,) if source else ())
        return Prompt(user=next_user, system=next_system, sources=sources)


@dataclass(frozen=True)
class PromptContext:
    """Complete prompt inputs for one agent session."""

    integration_name: str
    task: str
    action_model_name: str | None = None
    episode: Any | None = None
    session_dir: Path | None = None
    observation_profile: str = "none"
    action_profile: str | None = None
    skill_writing: bool = False
    interaction_mode: str = "tool_calling"
    tool_return_mode: str = "background"
    resource_scheduling: str = "per-resource"
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptContribution:
    """Capability-profile content that can be composed into the final prompt."""

    system: str | None = None
    user: str | None = None
    source: str | None = None


PromptBuilder = Callable[[PromptContext], Prompt]
TaskPromptBuilder = Callable[[str], Prompt]


def assemble_prompt(
    base: Prompt,
    context: PromptContext,
    contributions: tuple[PromptContribution, ...] = (),
) -> Prompt:
    """Append enabled runtime guidance, then persist the complete agent input."""
    prompts = get_prompts_dir()
    prompt = _append_resource(base, prompts / "runtime" / "common.md")
    if context.tool_return_mode not in {"immediate", "background"}:
        raise ValueError(f"Invalid tool return mode: {context.tool_return_mode}")
    if context.resource_scheduling not in {"per-resource", "global-serial"}:
        raise ValueError(f"Invalid resource scheduling: {context.resource_scheduling}")
    if context.interaction_mode not in {"tool_calling", "code_as_policy"}:
        raise ValueError(f"Invalid interaction mode: {context.interaction_mode}")
    resources = ("code_as_policy.md",) if context.interaction_mode == "code_as_policy" else (
        "tool_calling.md", "batch.md", f"resource_{context.resource_scheduling}.md", f"return_{context.tool_return_mode}.md",
    )
    for name in resources:
        prompt = _append_resource(prompt, prompts / "runtime" / name)
    if context.action_profile == "hybrid" and "actor_act" in context.tool_names:
        prompt = _append_resource(prompt, prompts / "runtime" / "hybrid.md")
        prompt = _append_integration_resource(prompt, context.integration_name, "hybrid.md")
    if context.skill_writing:
        namespace = context.integration_name
        if context.episode is not None:
            namespace = context.episode.description.metadata.get("skill_namespace", namespace)
        path = prompts / "guides" / "meta_skill.md"
        prompt = prompt.append(
            system=_render(path, {"skill_namespace": namespace}), source=str(path),
        )
    if context.observation_profile != "none":
        for contribution in contributions:
            prompt = prompt.append(
                system=contribution.system,
                user=contribution.user,
                source=contribution.source,
            )
    if context.session_dir is not None:
        path = prompts / "runtime" / "storage_access.md"
        prompt = prompt.append(
            system=_render(path, {
                "images_dir": str(context.session_dir / "images"),
                "scratch_dir": str(context.session_dir / "scratch"),
            }),
            source=str(path),
        )
        (context.session_dir / "effective_prompt.md").write_text(
            prompt.flatten(), encoding="utf-8",
        )
    return prompt


def _append_resource(prompt: Prompt, path: Path) -> Prompt:
    return prompt.append(system=path.read_text(encoding="utf-8"), source=str(path))


def _append_integration_resource(prompt: Prompt, integration: str, filename: str) -> Prompt:
    """Read optional environment-specific runtime notes only from bundled resources."""
    root = get_prompts_dir().resolve()
    path = (root / integration / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Integration prompt path must remain inside bundled resources")
    return _append_resource(prompt, path) if path.is_file() else prompt


def get_prompts_dir() -> Path:
    """Resolve bundled prompt resources independently of the working directory."""
    return Path(__file__).resolve().parents[1] / "resources" / "prompts"


def render_prompt_files(
    prompt_dir: str | Path,
    *,
    variables: Mapping[str, object],
) -> Prompt:
    """Render system.md and user.md while preserving message roles."""
    prompt_path = Path(prompt_dir)
    return Prompt(
        system=_render(prompt_path / "system.md", variables),
        user=_render(prompt_path / "user.md", variables),
        sources=(str(prompt_path / "system.md"), str(prompt_path / "user.md")),
    )


def _render(path: Path, variables: Mapping[str, object]) -> str:
    text = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"missing prompt variable {name!r} for {path}")
        return str(variables[name])

    return _PLACEHOLDER.sub(replace, text)


def _append_text(current: str | None, addition: str) -> str:
    """Join prompt sections using stable blank-line rules."""
    if not current:
        return addition
    return f"{current.rstrip()}\n\n{addition.lstrip()}"


_PROMPT_REGISTRY: dict[str, PromptBuilder] = {}


def register_prompt_builder(integration: str) -> Callable[[PromptBuilder], PromptBuilder]:
    """Register the environment builder for one integration."""
    def wrapper(builder: PromptBuilder) -> PromptBuilder:
        _PROMPT_REGISTRY[integration] = builder
        return builder

    return wrapper


def _load_builtin_builders() -> None:
    # Preserve registration order; load lightweight prompts without starting environments.
    import importlib

    importlib.import_module("uni_robot_agent.integrations.robodojo.prompt")


def get_prompt_builder(integration: str) -> PromptBuilder:
    """Resolve an environment builder, falling back to the generic base prompt."""
    _load_builtin_builders()
    return _PROMPT_REGISTRY.get(integration, _PROMPT_REGISTRY["*"])


def list_prompt_builders() -> tuple[str, ...]:
    """List integrations with registered environment builders."""
    _load_builtin_builders()
    return tuple(sorted(_PROMPT_REGISTRY))


@register_prompt_builder("*")
def _default_prompt(context: PromptContext) -> Prompt:
    return Prompt(
        system=(
            f"You are an embodied agent interacting with a prepared environment through the {context.integration_name!r} integration. "
            "Use the registered tools to complete the instruction and verify effects with current observations. "
            + (
                "Do not inspect simulator internals or service implementation sources. "
                "Use your own scripts to call the registered robot API."
                if context.interaction_mode == "code_as_policy" else
                "Do not inspect source code, scripts, processes, servers, or simulator internals. "
                "Do not use shell/exec or bypass the registered tools."
            )
        ),
        user=context.task,
    )
