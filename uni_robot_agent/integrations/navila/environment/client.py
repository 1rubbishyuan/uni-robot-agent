"""Own one Python 3.10 episode worker; ambiguous calls permanently poison it."""
import json
import os
from pathlib import Path
import select
import subprocess
import time

import numpy as np
from PIL import Image


class NativeClient:
    def __init__(self, python, root, *, task, seed, output):
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        self.capture_dir = output / "native-frames"
        self.log = (output / "native.log").open("a")
        self.failed, self.closed, self.sequence = False, False, 0
        self._state, self.ticks = None, 0
        try:
            self.process = subprocess.Popen([str(python), str(Path(__file__).with_name("worker.py")),
                "--native-root", str(Path(root).resolve()), "--task", task, "--seed", str(seed),
                "--capture-dir", str(self.capture_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log, text=True, bufsize=1,
                env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"})
        except BaseException:
            self.log.close()
            raise
        try:
            identity = self._receive(0, 900)
            self.instruction, self.seed = identity["instruction"], identity["seed"]
            self.policy_sha256 = identity["policy_sha256"]
        except BaseException:
            self.failed = True
            self.close()
            raise

    def _receive(self, request_id, timeout):
        deadline = time.monotonic() + timeout
        line = bytearray()
        while not line.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError("Native navigation acknowledgement timed out; execution is unknown")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk or len(line) + len(chunk) > 1024 * 1024:
                raise RuntimeError("Native navigation worker exited or exceeded its response limit")
            line.extend(chunk)
        reply = json.loads(line)
        if reply.get("request_id") != request_id:
            raise RuntimeError("Native navigation acknowledgement has a different request ID")
        if "error" in reply:
            raise RuntimeError("Native navigation failed: " + str(reply["error"]))
        return reply["result"]

    def _call(self, operation, **values):
        if self.failed or self.closed:
            raise RuntimeError("Native navigation worker is closed or execution is unknown")
        self.sequence += 1
        try:
            request = {"request_id": self.sequence, "operation": operation, **values}
            self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
            self.process.stdin.flush()
            return self._receive(self.sequence, 120)
        except BaseException as error:
            self.failed = True
            try:
                self._terminate()
            except Exception as cleanup_error:
                error.add_note(f"Native worker cleanup also failed: {cleanup_error}")
            raise

    def frame(self):
        reply = self._call("frame")
        if reply.get("tick") != self.ticks:
            self.failed = True
            self._terminate()
            raise ValueError("Native camera tick differs from the last acknowledged state")
        path = Path(reply["path"]).resolve()
        if not path.is_relative_to(self.capture_dir) or path.suffix != ".png":
            self.failed = True
            raise ValueError("Native camera path escaped its episode capture directory")
        with Image.open(path) as image:
            if image.mode != "RGB":
                raise ValueError("Native front camera must be RGB")
            return np.asarray(image).copy()

    def measured_state(self):
        if self._state is None:
            self._state = self._call("state")
        return dict(self._state)

    def step(self, velocity):
        reply = self._call("step", velocity=velocity)
        if reply.get("tick") != self.ticks + 1 or type(reply.get("done")) is not bool:
            self.failed = True
            self._terminate()
            raise ValueError("Native step did not acknowledge exactly one control tick")
        self.ticks = reply["tick"]
        self._state = reply["state"]
        return reply["done"]

    def finish(self, stopped):
        return self._call("finish", stopped=stopped)

    def _terminate(self):
        self.closed = True
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
            self.log.close()

    def close(self):
        if self.closed:
            return
        try:
            if not self.failed and self.process.poll() is None:
                self._call("close")
        finally:
            self._terminate()
