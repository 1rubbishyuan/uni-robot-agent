"""Pinned GR00T source, checkpoint identity and native policy construction."""
import hashlib
import importlib
from pathlib import Path
import subprocess
import sys

from .config import GR00T_REVISION


def validate_source(path):
    root = Path(path).expanduser().resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True)
    if revision != GR00T_REVISION or dirty:
        raise ValueError(f"GR00T requires a clean source checkout at {GR00T_REVISION}")
    return root


def identify_checkpoint(path):
    root = Path(path).expanduser().resolve(strict=True)
    if not (root / "config.json").is_file() or not list(root.glob("*.safetensors")):
        raise ValueError("GR00T checkpoint requires config.json and safetensors weights")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        file_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                file_digest.update(block)
        digest.update(str(relative).encode() + b"\0" + file_digest.digest())
    return digest.hexdigest()


def import_native(root):
    root = validate_source(root)
    sys.path.insert(0, str(root))
    module = importlib.import_module("gr00t.policy.gr00t_policy")
    if not Path(module.__file__).resolve().is_relative_to(root):
        raise ValueError("Imported GR00T policy differs from the pinned source")
    return module


def validate_modalities(policy, config):
    modalities = policy.get_modality_config()
    expected = {"video": (set(config.camera_mapping), [0]),
                "state": (set(config.observation_mapping), [0]),
                "language": ({config.language_key}, [0]),
                "action": (set(config.action_mapping), list(range(config.profile.action_horizon)))}
    for name, (keys, indices) in expected.items():
        actual = modalities.get(name)
        if actual is None or set(actual.modality_keys) != keys or list(actual.delta_indices) != indices:
            raise ValueError(f"Checkpoint {name} modalities do not match the declared GR00T profile")


def load_policy(config):
    module = import_native(config.native_root)
    actual = identify_checkpoint(config.checkpoint)
    if actual != config.checkpoint_sha256:
        raise ValueError("GR00T checkpoint checksum mismatch")
    from gr00t.data.embodiment_tags import EmbodimentTag
    policy = module.Gr00tPolicy(embodiment_tag=EmbodimentTag[config.embodiment_tag],
        model_path=str(config.checkpoint), device=config.device, strict=True)
    validate_modalities(policy, config)
    return policy
