"""One exclusive native GR00T policy behind the shared actor service."""
import hashlib
import json
import threading

from uni_robot_agent.services.actor.contracts import ActorInfo, NumericChunk
from uni_robot_agent.services.errors import ServiceError

from .mapping import action_matrix, observation_payload
from .native_compat import load_policy


class Gr00tBackend:
    def __init__(self, config):
        self.config = config
        self.policy = load_policy(config)
        self.info = ActorInfo(model=config.model, runtime=config.runtime,
            model_id=hashlib.sha256(json.dumps(config.identity(), sort_keys=True).encode()).hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256, model_config_name=config.model_config_name,
            profile=config.profile)
        self._lock = threading.Lock()
        self._active = None
        self._failed = self._closed = False

    def create_session(self):
        with self._lock:
            if self._closed or self._failed or self._active is not None:
                raise ServiceError("upstream_unavailable", "GR00T requires an exclusive healthy policy", status=409,
                                   details={"accepted": False})
            try:
                self.policy.reset()
            except Exception:
                self._failed = True
                raise
            self._active = object()
            return self._active

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("This GR00T backend admits one request at a time")
        work = requests[0]
        with self._lock:
            if self._closed or self._failed or self._active is None or work.state is not self._active:
                return [RuntimeError("GR00T session is unavailable")]
            try:
                actions, _ = self.policy.get_action(observation_payload(work.observation, self.config))
                return [NumericChunk(actions=action_matrix(actions, self.config))]
            except Exception as error:
                # A failed prediction can advance native model state; never replay it.
                self._failed = True
                return [error]

    def close_session(self, state):
        with self._lock:
            if state is self._active:
                self._active = None

    def close(self):
        with self._lock:
            self._closed = True
            self._active = None
            self.policy = None
