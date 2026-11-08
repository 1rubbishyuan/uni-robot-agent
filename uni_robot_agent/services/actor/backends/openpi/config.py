"""Native OpenPI configuration; model semantics are supplied by the caller."""
import tomllib
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uni_robot_agent.services.actor.contracts import ActorProfile


class OpenpiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "pi05"
    runtime: Literal["openpi_jax"] = "openpi_jax"
    checkpoint: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config_name: str
    profile: ActorProfile
    native_root: str
    source_revision: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_files_sha256: dict[str, str] = Field(default_factory=dict)
    pythonpath: list[str] = Field(min_length=1)
    assets_id: str
    working_directory: str | None = None
    inference_mode: Literal["native", "single"]
    image_keys: dict[str, str]
    image_container: str = "images"
    image_layout: Literal["HWC", "CHW"] = "HWC"
    resize: tuple[int, int] | None = None
    policy_rng_seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def input_layout(self):
        if set(self.image_keys) != set(self.profile.cameras):
            raise ValueError("OpenPI image mapping must cover exactly the actor cameras")
        if len(set(self.image_keys.values())) != len(self.image_keys):
            raise ValueError("OpenPI image mapping must have distinct destination keys")
        if self.image_container in {"state", "prompt"}:
            raise ValueError("OpenPI image_container cannot replace state or prompt")
        if not self.image_container and set(self.image_keys.values()) & {"state", "prompt"}:
            raise ValueError("Top-level image keys cannot replace state or prompt")
        if self.resize is not None and min(self.resize) < 1:
            raise ValueError("OpenPI resize dimensions must be positive")
        return self


def recipe_settings(name: str) -> dict:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name):
        raise ValueError("Invalid actor recipe name")
    resource = resources.files("uni_robot_agent.services.actor").joinpath(f"recipes/{name}.toml")
    return tomllib.loads(resource.read_text())


def resolve_config(settings: dict, *, legacy: bool = False) -> OpenpiConfig:
    """Expand named recipes and the old pi05 CLI into one native configuration."""
    values = dict(settings)
    if "xpolicylab_root" in values:
        if "native_root" in values:
            raise ValueError("Use only one of native_root and xpolicylab_root")
        values["native_root"] = values.pop("xpolicylab_root")
    selected = values.get("profile", "robodojo_pi05" if legacy else None)
    if isinstance(selected, str):
        if legacy and selected != "robodojo_pi05":
            raise ValueError("The pi05 alias only selects robodojo_pi05; use backend=openpi")
        defaults = recipe_settings(selected)["actor"]
        if legacy and values.get("model_config_name", defaults["model_config_name"]) != defaults["model_config_name"]:
            raise ValueError("The pi05 alias requires its original training configuration")
        values.pop("profile", None)
        values = {**defaults, **values}
    return OpenpiConfig.model_validate(values)
