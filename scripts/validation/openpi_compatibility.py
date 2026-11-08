"""Compare a native OpenPI actor against recorded fixed-input model predictions.

The NPZ contains state[N,D], prompt[N] (Unicode), images_CAMERA[N,H,W,3],
actions[N,H,A], optionally actions_sequence[S,N,H,A], and actions_single[S,N,H,A].
Generate the reference
with the pinned upstream implementation before a behavior-preserving change.
Run each model in its own process and native Python environment.
"""
import argparse
import json
import os
import tomllib
from pathlib import Path

import numpy as np

from uni_robot_agent.services.actor.backend import ActorWork
from uni_robot_agent.services.actor.backends.openpi.backend import OpenpiBackend
from uni_robot_agent.services.actor.backends.openpi.config import resolve_config
from uni_robot_agent.services.actor.contracts import ActorObservation
from uni_robot_agent.services.actor.images import encode_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--atol", type=float, default=1e-5)
    parser.add_argument("--rtol", type=float, default=1e-5)
    args = parser.parse_args()
    settings = tomllib.loads(args.config.read_text())
    legacy = settings.pop("backend", "openpi") == "pi05"
    for key in ("http", "batching", "sessions"):
        settings.pop(key, None)
    config = resolve_config(settings, legacy=legacy)
    config = config.model_copy(update={"native_root": str(Path(config.native_root).expanduser().resolve()),
                                       "checkpoint": str(Path(config.checkpoint).expanduser().resolve())})
    if args.output:
        args.output = args.output.resolve()
    with np.load(args.reference, allow_pickle=False) as archive:
        data = dict(archive)
    expected = data.get("actions_sequence", data["actions"][None, ...])
    observations = [ActorObservation(state=state.tolist(), instruction=str(data["prompt"][i]),
        images={camera: encode_image(data[f"images_{camera}"][i]) for camera in config.profile.cameras})
        for i, state in enumerate(data["state"])]
    if config.working_directory is not None:
        # This is the actor process entry point, before importing either native fork.
        os.chdir(Path(config.native_root) / config.working_directory)
    model = OpenpiBackend(config)
    try:
        def run(grouped):
            sessions = [model.create_session() for _ in observations]
            sequence = []
            try:
                for _ in range(len(expected)):
                    work = [ActorWork(state, obs) for state, obs in zip(sessions, observations, strict=True)]
                    if grouped:
                        results = model.predict_batch(work)
                    else:
                        results = [model.predict_batch([item])[0] for item in work]
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                    sequence.append(results)
            finally:
                for session in sessions:
                    model.close_session(session)
            return np.asarray(sequence, dtype=np.float32)

        actual = run(config.inference_mode == "native")
        independent = run(False)
        np.testing.assert_allclose(actual, expected, atol=args.atol, rtol=args.rtol)
        single_error = None
        if "actions_single" in data:
            np.testing.assert_allclose(independent, data["actions_single"], atol=args.atol, rtol=args.rtol)
            single_error = float(np.max(np.abs(independent - data["actions_single"])))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(args.output, **{**data, "actions": actual[0], "actions_sequence": actual, "actions_single": independent})
        print(json.dumps({"model_id": model.info.model_id, "shape": list(actual.shape),
            "reference_max_abs_error": float(np.max(np.abs(actual - expected))),
            "single_reference_max_abs_error": single_error,
            "batch_single_numerical_gap": float(np.max(np.abs(actual - independent))),
            "source_sha256": model.runtime.identity["source_sha256"]}, indent=2))
    finally:
        model.close()


if __name__ == "__main__":
    main()
