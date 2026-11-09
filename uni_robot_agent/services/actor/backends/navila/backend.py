"""Bridge one exclusive native NaVILA server without simulator dependencies."""
import hashlib
import json
import threading

from uni_robot_agent.services.actor.contracts import ActorInfo, SymbolicPrediction
from uni_robot_agent.services.errors import ServiceError

from .config import CHECKPOINT_REVISION, REVISION
from .decoding import decode_action
from .native_compat import predict
from .preprocessing import prepare_history


class NaVILABackend:
    def __init__(self, config):
        self.config = config
        identity = {"runtime": config.runtime, "model": config.model, "source_revision": REVISION,
            "checkpoint_revision": CHECKPOINT_REVISION, "checkpoint_sha256": config.checkpoint_sha256,
            "preprocessing": "episode_uniform_8_jpeg75", "decoding": "strict_single_command_v1",
            "identity_assurance": "operator_declared"}
        self.info = ActorInfo(model=config.model, runtime=config.runtime,
            model_id=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256, profile=config.profile,
            model_config_name=config.model_config_name)
        self._lock = threading.Lock()
        self._active, self._failed, self._closed = None, False, False

    def create_session(self):
        with self._lock:
            if self._active is not None or self._failed or self._closed:
                raise ServiceError("upstream_unavailable", "NaVILA needs one exclusive healthy upstream",
                    status=409, details={"accepted": False})
            self._active = object()
            return self._active

    def close_session(self, state):
        with self._lock:
            if state is self._active:
                self._active = None

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("NaVILA admits one native inference at a time")
        work = requests[0]
        with self._lock:
            if self._closed or self._failed or self._active is None or work.state is not self._active:
                return [RuntimeError("NaVILA session is unavailable")]
            try:
                images, evidence = prepare_history(work.observation.history["front"])
                text = predict(self.config, images, work.observation.instruction)
                label, parameters = decode_action(text)
                return [SymbolicPrediction(label=label, parameters=parameters,
                    evidence={**evidence, "native_output": text, "identity_assurance": "operator_declared"})]
            except Exception as error:
                self._failed = True
                return [error]

    def close(self):
        with self._lock:
            self._closed = True
            self._active = None
