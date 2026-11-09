"""Bridge one exclusive upstream model through the existing actor service."""

import hashlib
import json
import threading

from uni_robot_agent.services.actor.contracts import ActorInfo, NumericChunk
from uni_robot_agent.services.errors import ServiceError

from .client import XPolicyLabClient
from .config import XPolicyLabConfig
from .mapping import action_matrix, observation_payload


class XPolicyLabBackend:
    def __init__(self, config: XPolicyLabConfig):
        self.config = config
        self.info = ActorInfo(
            model=config.model, runtime="xpolicylab_ws",
            model_id=hashlib.sha256(json.dumps(config.identity(), sort_keys=True).encode()).hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256,
            model_config_name=config.model_config_name,
            profile=config.profile,
        )
        self._lock = threading.Lock()
        self._active = None
        self._failed = False
        self._closed = False

    def create_session(self):
        with self._lock:
            if self._closed or self._failed or self._active is not None:
                raise ServiceError("upstream_unavailable",
                    "XPolicyLab requires an exclusive healthy upstream; restart after a failure",
                    status=409, details={"accepted": False})
            client = None
            try:
                client = XPolicyLabClient(self.config)
                client.reset()
            except Exception:
                # A failed reset may still be running; a new session cannot race it.
                self._failed = True
                if client is not None:
                    client.close()
                raise
            self._active = client
            return client

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("XPolicyLab bridge supports one request at a time")
        work = requests[0]
        with self._lock:
            if self._closed or self._failed or self._active is None or work.state is not self._active:
                return [RuntimeError("XPolicyLab session is not available")]
            try:
                observation = observation_payload(work.observation, self.config)
                actions = self._active.infer(observation)
                return [NumericChunk(actions=action_matrix(actions, self.config))]
            except Exception as error:  # noqa: BLE001 - report upstream failures through actor receipts.
                # No replay after an ambiguous inference or invalid action result.
                self._failed = True
                return [error]

    def close_session(self, state):
        with self._lock:
            if state is not self._active or state is None:
                return
            try:
                if not self._failed:
                    state.end_trial()
            except Exception:
                self._failed = True
                raise
            finally:
                self._active = None
                try:
                    state.close()
                except Exception:
                    self._failed = True
                    raise

    def close(self):
        with self._lock:
            self._closed = True
            active = self._active
        self.close_session(active)
