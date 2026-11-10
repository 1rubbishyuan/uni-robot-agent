"""Resolve one actor recipe and backend before importing native runtimes."""
from dataclasses import dataclass

from pydantic import TypeAdapter

from uni_robot_agent.services.deployment.config import Recipe

from .registry import BackendDefinition, get_backend


def load_recipe(name: str) -> Recipe:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name):
        raise ValueError("Invalid actor recipe name")
    return Recipe.load("uni_robot_agent.services.actor", f"recipes/{name}.toml")


def recipe_backend(recipe):
    # Existing recipes predate backend selection; their bytes and cache identities stay intact.
    return get_backend(recipe.settings.get("backend", "openpi"))


@dataclass(frozen=True)
class ResolvedActor:
    definition: BackendDefinition
    config: object


def resolve_actor(settings: dict, *, backend: str | None = None) -> ResolvedActor:
    values = dict(settings)
    configured_backend = values.pop("backend", None)
    selected = values.get("profile")
    recipe = load_recipe(selected) if isinstance(selected, str) else None
    requested = backend or configured_backend or (recipe_backend(recipe).name if recipe else "pi05")
    definition = get_backend(requested)
    if requested == "pi05":
        if selected is None:
            selected = "robodojo_pi05"
            recipe = load_recipe(selected)
        elif selected != "robodojo_pi05":
            raise ValueError("The pi05 alias only selects robodojo_pi05; use backend=openpi")
    if recipe is not None:
        if recipe_backend(recipe).name != definition.name:
            raise ValueError(f"Actor recipe {selected!r} belongs to {recipe_backend(recipe).name}, not {definition.name}")
        defaults = recipe.settings["actor"]
        if requested == "pi05" and values.get("model_config_name", defaults["model_config_name"]) != defaults["model_config_name"]:
            raise ValueError("The pi05 alias requires its original training configuration")
        values.pop("profile", None)
        values = {**defaults, **values}
    return ResolvedActor(definition, definition.resolve_config(values))


def apply_service_limits(resolved, batching, sessions):
    """Default omitted capacities and reject explicitly incompatible service settings."""
    limits = resolved.definition.service_limits(resolved.config)
    for name, maximum in limits.items():
        if name not in {"max_batch_size", "max_sessions"} or type(maximum) is not int or maximum < 1:
            raise ValueError("Actor service limits must declare positive batch or session capacities")
        settings = batching if name == "max_batch_size" else sessions
        value = TypeAdapter(int).validate_python(settings.setdefault(name, maximum))
        if value > maximum:
            raise ValueError(f"Actor backend {resolved.definition.name} requires {name}<={maximum}")
