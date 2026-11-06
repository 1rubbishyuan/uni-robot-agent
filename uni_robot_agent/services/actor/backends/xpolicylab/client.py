"""Compatibility boundary for the pinned upstream WebSocket client.

The upstream synchronous facade lacks an atomic inference method. Access to its
loop/client internals stays here and is guarded by source hashes for the full
client import closure at XPOLICYLAB_REVISION. No model code is imported.
"""

import hashlib
import importlib
import sys
from pathlib import Path
from uuid import uuid4

from .config import XPOLICYLAB_REVISION, XPolicyLabConfig

CLIENT_HASHES = {
    "__init__.py": "4717f99f5db6d3e0496032f1678005efc829d071037db6433bc9dac7f5ae680e",
    "ws/__init__.py": "79905987a12ebf318dc0b2b9ec6f375d9b4561bac29d6c27d04e318d8f6e00b3",
    "ws/model_client.py": "4d6e225ae5f577d862366350dca8742e91dd9527d6e76b8b2a6d48c991212d67",
    "ws/protocol/__init__.py": "19aeae375e94e58d437d789d0aff07b232c13f375235384d784984631d25d5d9",
    "ws/protocol/client.py": "b309f58b1bddd99e56578026b61a15e6cfdc07e843e8a082bfd291d0e853ce5a",
    "ws/protocol/codec.py": "8a97ef7e102fd954c266352d81eaceef0f1b0f5504571c03037683efe387865a",
    "ws/protocol/exceptions.py": "73161f4cdcc7ffc8a87cec94f5459bfcb9113076be272d6cb2a3ff8d69e0c6ce",
    "ws/protocol/keepalive.py": "7f6b9e636eefcd6c80a4b62e404a6f89e2f7e1a3b6008500312a972d6990a21d",
    "ws/protocol/messages.py": "d05c8cbc20e0cbe1502551864907f25897035993f920748c9290c944c959eab7",
    "ws/protocol/schemas.py": "f1ca82eafa8dcad6d968508adb51c25342b291f7b1c553fff305c381a6098bd0",
}


def load_client(root: Path):
    """Load only the reviewed client sources, rejecting module-name collisions."""
    root = root.expanduser().resolve()
    for relative, expected in CLIENT_HASHES.items():
        path = root / "client_server" / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"XPolicyLab client source mismatch: {relative}; use {XPOLICYLAB_REVISION}")
    for name, module in tuple(sys.modules.items()):
        if (name == "client_server" or name.startswith("client_server.")) and (
            not getattr(module, "__file__", None)
            or not Path(module.__file__).resolve().is_relative_to(root)
        ):
            raise RuntimeError("A different client_server package is already loaded")
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module("client_server.ws.model_client").WsModelClient
    finally:
        sys.path.remove(str(root))


class XPolicyLabClient:
    def __init__(self, config: XPolicyLabConfig):
        client_type = load_client(config.xpolicylab_root)
        self.trial_id = uuid4().hex
        self.step = 0
        self.client = client_type(
            url=config.upstream_url, evaluation_id="uni-robot-agent",
            trial_id=self.trial_id, request_timeout_s=config.request_timeout_s,
            max_connect_attempts=1, max_connect_seconds=30,
            connect_timeout_s=10, handshake_timeout_s=10,
        )
        if self.client._client._server_instance_id is None:
            self.client.close()
            raise ValueError("XPolicyLab must advertise server_instance_id for restart detection")

    def reset(self):
        self.client.call("reset")

    def infer(self, observation: dict):
        # One INFER holds the upstream model lock across update_obs/get_action.
        reply = self.client._run(self.client._client.infer(
            observation, trial_id=self.trial_id, step=self.step,
        ))
        self.step += 1
        return reply.payload["actions"]

    def end_trial(self):
        self.client.call("trial_end")

    def close(self):
        self.client.close()
