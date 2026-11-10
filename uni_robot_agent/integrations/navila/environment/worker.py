"""Standalone native entry point. Never import the harness package here."""
import argparse
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--runtime-profile", choices=["isaac41", "isaac51"], default="isaac41")
    parser.add_argument("--record-native-frames", action="store_true")
    args = parser.parse_args()
    wire = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    native = None
    expected_id = 1
    try:
        with redirect_stdout(sys.stderr):
            import numpy as np
            from PIL import Image
            from native_compat import CONTROL_DT, create_native
            native = create_native(args.native_root, task=args.task, seed=args.seed,
                                   runtime_profile=args.runtime_profile)
        args.capture_dir.mkdir(parents=True, exist_ok=False)
        ticks, request_id = 0, 0

        def capture_frame():
            path = args.capture_dir / f"front-{ticks:08d}.png"
            if not path.exists():
                captured_at = time.time()
                temporary = path.with_suffix(".tmp")
                Image.fromarray(native.frame()).save(temporary, format="PNG")
                os.replace(temporary, path)
                record = {"tick": ticks, "native_tick": ticks, "native_request_id": request_id,
                          "simulation_time": ticks * CONTROL_DT,
                          "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "captured_at": captured_at, "camera": "front"}
                with (args.capture_dir / "index.jsonl").open("a") as index:
                    index.write(json.dumps(record, allow_nan=False) + "\n")
            return path

        if args.record_native_frames:
            capture_frame()
        print(json.dumps({"request_id": 0, "result": {"instruction": native.instruction,
            "seed": native.seed, "policy_sha256": native.policy_sha256, "runtime": native.runtime}}), file=wire, flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            request_id = request["request_id"]
            if type(request_id) is not int or request_id != expected_id:
                raise ValueError("Native worker requires consecutive request IDs without replay")
            expected_id += 1
            operation = request["operation"]
            with redirect_stdout(sys.stderr):
                if operation == "step":
                    velocity = np.asarray(request["velocity"], dtype=float)
                    if velocity.shape != (3,) or not np.isfinite(velocity).all():
                        raise ValueError("Native velocity must contain three finite values")
                    done = native.step(velocity.tolist())
                    ticks += 1
                    if args.record_native_frames:
                        capture_frame()
                    result = {"done": done, "state": native.measured_state(), "tick": ticks}
                elif operation == "frame":
                    result = {"path": str(capture_frame()), "tick": ticks}
                elif operation == "state":
                    result = native.measured_state()
                elif operation == "finish":
                    if type(request["stopped"]) is not bool:
                        raise ValueError("Navigation completion must be an explicit boolean")
                    result = native.finish(request["stopped"])
                elif operation == "close":
                    native.close()
                    result = {"closed": True}
                else:
                    raise ValueError("Unsupported native worker operation")
            print(json.dumps({"request_id": request_id, "result": result}, allow_nan=False), file=wire, flush=True)
            if operation == "close":
                break
    except BaseException as error:
        # A native exception may follow physical motion. End this process; never replay.
        print(json.dumps({"request_id": locals().get("request_id", 0), "error": str(error)}), file=wire, flush=True)
        raise
    finally:
        if native is not None:
            with redirect_stdout(sys.stderr):
                native.close()


if __name__ == "__main__":
    main()
