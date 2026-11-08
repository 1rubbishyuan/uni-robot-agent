"""Compatibility boundary for explicitly pinned OpenPI/JAX source trees."""
import dataclasses
import hashlib
import json
import sys
from pathlib import Path


def source_hash(root):
    root = Path(root).resolve()
    files = sorted(root.rglob("*.py"))
    if not files:
        raise ValueError("OpenPI source contains no Python files")
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest(), len(files)


def checkpoint_identity(path, *, assets_id, config_name, action_dim, action_horizon, seed):
    path = Path(path).resolve()
    if (path / "model.safetensors").exists():
        raise ValueError("OpenPI/JAX requires a params checkpoint without model.safetensors")
    hashes = {}
    for folder in ("params", "assets"):
        for file in sorted((path / folder).rglob("*")):
            if file.is_file():
                digest = hashlib.sha256()
                with file.open("rb") as stream:
                    for part in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(part)
                hashes[str(file.relative_to(path))] = digest.hexdigest()
    if not any(key.startswith("params/") for key in hashes) or f"assets/{assets_id}/norm_stats.json" not in hashes:
        raise ValueError(f"OpenPI params and assets/{assets_id}/norm_stats.json are required")
    return {
        "checkpoint": str(path), "config": config_name, "backend": "OpenPI/JAX",
        "checkpoint_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "files_sha256": hashes, "action_dim": action_dim,
        "action_horizon": action_horizon, "policy_rng_seed": seed,
    }


def identify_checkpoint(settings):
    return checkpoint_identity(settings.checkpoint, assets_id=settings.assets_id,
        config_name=settings.model_config_name, action_dim=len(settings.profile.action_fields),
        action_horizon=settings.profile.action_horizon, seed=settings.policy_rng_seed)


def import_native(settings):
    """A process owns one fork; never replace already imported OpenPI modules."""
    root = Path(settings.native_root).resolve()
    source_digest, source_files = source_hash(root)
    if source_digest != settings.source_sha256:
        raise RuntimeError("OpenPI Python source differs from the pinned recipe")
    for relative, expected in settings.source_files_sha256.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"OpenPI source configuration differs from the pinned recipe: {relative}")
    if settings.working_directory is not None:
        working = (root / settings.working_directory).resolve()
        if not working.is_relative_to(root):
            raise ValueError("OpenPI working_directory must remain inside native_root")
        if Path.cwd().resolve() != working:
            raise RuntimeError("OpenPI must be launched in its configured working_directory")
    paths = [(root / relative).resolve() for relative in settings.pythonpath]
    if any(not path.is_relative_to(root) or not path.is_dir() for path in paths):
        raise ValueError("OpenPI pythonpath must identify directories inside native_root")
    sys.path[:0] = list(map(str, paths))
    import openpi
    import openpi_client

    for module in (openpi, openpi_client):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("Imported OpenPI differs from the pinned checkout; use a separate actor process")
    return {"source_revision": settings.source_revision, "source_sha256": source_digest,
            "source_files": source_files, "source_files_sha256": settings.source_files_sha256}


@dataclasses.dataclass(frozen=True)
class LoadedPolicy:
    policy: object
    identity: dict
    action_horizon: int
    latent_action_dim: int


def validate_model_config(cfg, profile):
    if cfg.model.model_type.value not in {"pi0", "pi05"}:
        raise ValueError("OpenPI/JAX currently supports noise-injected diffusion inference (Pi0/Pi05), not FAST")
    if cfg.model.action_horizon != profile.action_horizon:
        raise ValueError("OpenPI training horizon differs from the actor profile")


def load_policy(settings):
    source = import_native(settings)
    from openpi.policies.policy_config import create_trained_policy
    from openpi.training import config

    cfg = config.get_config(settings.model_config_name)
    validate_model_config(cfg, settings.profile)
    identity = identify_checkpoint(settings)
    if identity["checkpoint_sha256"] != settings.checkpoint_sha256:
        raise RuntimeError("Checkpoint identity does not match the launch manifest")
    factory = dataclasses.replace(cfg.data, assets=config.AssetsConfig(
        assets_dir=str(Path(settings.checkpoint).resolve() / "assets"), asset_id=settings.assets_id))
    cfg = dataclasses.replace(cfg, data=factory)
    # Keep the fork's input, normalization, tokenization and output transforms intact.
    policy = create_trained_policy(cfg, settings.checkpoint)
    if identify_checkpoint(settings) != identity:
        raise RuntimeError("Checkpoint changed during load")
    identity.update(source)
    return LoadedPolicy(policy, identity, cfg.model.action_horizon, cfg.model.action_dim)
