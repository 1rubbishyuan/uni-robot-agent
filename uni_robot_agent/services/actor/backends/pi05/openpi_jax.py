"""Pinned OpenPI/JAX runtime; no network server or second batching queue."""

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from uni_robot_agent.services.actor.images import decode_image

from .config import CAMERAS, ROBODOJO_CONFIG

XPOLICYLAB_COMMIT = "432f82b1758c5b1202e42a3dfe014546dbc50871"
OPENPI_SOURCE_SHA256 = (
    "807d83318fe6c48da1cb4da6627e2313cf072561c756fe8a754d4285d33ac229"
)


def source_hash(root):
    root = Path(root).resolve()
    files = sorted(root.rglob("*.py"))
    if not files:
        raise ValueError("XPolicyLab source contains no Python files")
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest(), len(files)


def checkpoint_identity(path):
    path = Path(path).resolve()
    hashes = {}
    for folder in ("params", "assets"):
        for file in sorted((path / folder).rglob("*")):
            if file.is_file():
                digest = hashlib.sha256()
                with file.open("rb") as stream:
                    for part in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(part)
                hashes[str(file.relative_to(path))] = digest.hexdigest()
    if (
        not any(key.startswith("params/") for key in hashes)
        or "assets/arx_x5_sim/norm_stats.json" not in hashes
    ):
        raise ValueError("Official pi05 params and arx_x5_sim normalizer required")
    return {
        "checkpoint": str(path),
        "config": ROBODOJO_CONFIG,
        "backend": "OpenPI/JAX",
        "checkpoint_sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
        "files_sha256": hashes,
        "action_dim": 14,
        "action_horizon": 50,
        "policy_rng_seed": 0,
    }


@dataclasses.dataclass
class _Session:
    index: int = 0
    rng: object = None


def _sample_noise(session):
    import jax

    if session.rng is None:
        session.rng = jax.random.key(0)
    session.rng, sample_rng = jax.random.split(session.rng)
    # Preserve per-episode split order and sample shape so batching does not alter noise.
    return np.asarray(jax.random.normal(sample_rng, (1, 50, 32)))[0]


def _prepare_observation(observation):
    from openpi_client.image_tools import resize_with_pad

    state = np.asarray(observation["state"])
    if state.shape != (14,) or not np.isfinite(state).all():
        raise ValueError("Expected finite 14-dimensional state")
    if not isinstance(observation["prompt"], str):
        raise TypeError("Expected one prompt string per request")
    if set(observation["images"]) != set(CAMERAS):
        raise ValueError(f"Expected cameras {CAMERAS}")
    images = {}
    for camera in CAMERAS:
        image = np.asarray(observation["images"][camera])
        if image.ndim != 3 or image.shape[0] != 3 or image.dtype != np.uint8:
            raise ValueError("Expected uint8 CHW images")
        if min(image.shape[1:]) < 1:
            raise ValueError("Expected nonempty images")
        # Use the pinned configuration's PIL resize to batch images of different resolutions.
        image = resize_with_pad(image.transpose(1, 2, 0), 224, 224)
        images[camera] = image.transpose(2, 0, 1)
    return {"state": state, "prompt": observation["prompt"], "images": images}


class OpenpiJaxRuntime:
    def __init__(self, settings):
        if (
            settings.profile != "robodojo_pi05"
            or settings.model_config_name != ROBODOJO_CONFIG
        ):
            raise ValueError(
                "OpenPI runtime requires the pinned ARX-X5 training profile"
            )
        if not settings.xpolicylab_root or not settings.checkpoint_sha256:
            raise ValueError(
                "OpenPI runtime requires source root and checkpoint SHA256"
            )
        root = Path(settings.xpolicylab_root).resolve()
        checkpoint = Path(settings.checkpoint).resolve()
        source_digest, source_files = source_hash(root)
        if source_digest != OPENPI_SOURCE_SHA256:
            raise RuntimeError("XPolicyLab Python source differs from pinned copy")
        sys.path.insert(0, str(root / "policy/Pi_05/openpi/src"))
        sys.path.insert(0, str(root / "policy/Pi_05/openpi/packages/openpi-client/src"))
        import openpi
        from openpi.policies.policy_config import create_trained_policy
        from openpi.training import config

        if not Path(openpi.__file__).resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Imported OpenPI differs from the pinned checkout")
        identity = checkpoint_identity(checkpoint)
        if identity["checkpoint_sha256"] != settings.checkpoint_sha256:
            raise RuntimeError("Checkpoint identity does not match the launch manifest")
        cfg = config.get_config(settings.model_config_name)
        factory = dataclasses.replace(
            cfg.data,
            assets=config.AssetsConfig(
                assets_dir=str(checkpoint.resolve() / "assets"), asset_id="arx_x5_sim"
            ),
        )
        cfg = dataclasses.replace(cfg, data=factory)
        policy = create_trained_policy(cfg, checkpoint)
        if checkpoint_identity(checkpoint) != identity:
            raise RuntimeError("Checkpoint changed during load")
        self.policy, self.identity = policy, identity
        self.identity.update(
            xpolicylab_commit=XPOLICYLAB_COMMIT,
            source_sha256=source_digest,
            source_files=source_files,
        )

    def create_session(self):
        return _Session()

    def close_session(self, state):
        state.rng = None

    def close(self):
        self.policy = None

    def predict_batch(self, requests):
        prepared = []
        results = [None] * len(requests)
        for index, work in enumerate(requests):
            try:
                observation = {
                    "state": work.observation.state,
                    "prompt": work.observation.instruction,
                    "images": {
                        name: decode_image(image).transpose(2, 0, 1)
                        for name, image in work.observation.images.items()
                    },
                }
                prepared.append((index, work.state, observation))
            except Exception as exc:  # noqa: BLE001 - isolate image decode failures.
                results[index] = exc
        if prepared:
            batch = self._process_batch([(state, obs) for _, state, obs in prepared])
            for (index, state, _), result in zip(prepared, batch, strict=True):
                if isinstance(result, BaseException):
                    results[index] = result
                else:
                    results[index] = result["actions"].tolist()
                    state.index += 1
        return results

    def _process_batch(self, requests):
        results = [None] * len(requests)
        valid = []
        for index, (session, observation) in enumerate(requests):
            try:
                observation = _prepare_observation(observation)
                valid.append((index, session, observation, _sample_noise(session)))
            except Exception as exc:  # noqa: BLE001 - isolate malformed requests.
                results[index] = exc
        if not valid:
            return results
        batch = {
            "state": np.stack([item[2]["state"] for item in valid]),
            "prompt": [item[2]["prompt"] for item in valid],
            "images": {
                camera: np.stack([item[2]["images"][camera] for item in valid])
                for camera in CAMERAS
            },
        }
        try:
            # Infer the whole batch in one call; never loop over requests or silently serialize them.
            output = self.policy.infer(
                batch, noise=np.stack([item[3] for item in valid])
            )
            actions = np.asarray(output["actions"], dtype=np.float32)
            if actions.shape != (len(valid), 50, 14):
                raise RuntimeError("Expected B x 50 x 14 pi05 actions")
            for row, (index, session, _, _) in enumerate(valid):
                if not np.isfinite(actions[row]).all():
                    results[index] = RuntimeError("Expected finite pi05 actions")
                    continue
                results[index] = {
                    "actions": actions[row],
                    "policy_identity": {
                        "checkpoint_sha256": self.identity["checkpoint_sha256"],
                        "inference_index": session.index,
                    },
                    "policy_timing": output.get("policy_timing", {}),
                    "batch_size": len(valid),
                }
        except Exception as exc:  # noqa: BLE001 - return model failure to every caller.
            for index, *_ in valid:
                results[index] = exc
        return results
