"""One native OpenPI actor, independent of the environment integration."""
import hashlib
import json

from uni_robot_agent.services.actor.contracts import ActorInfo, NumericChunk

from .config import OpenpiConfig


class OpenpiBackend:
    def __init__(self, config: OpenpiConfig):
        from .openpi_jax import OpenpiJaxRuntime

        self.runtime = OpenpiJaxRuntime(config)
        checkpoint_sha256 = self.runtime.identity["checkpoint_sha256"]
        # Paths locate artifacts; checksums and all inference settings identify behavior.
        model_identity = config.model_dump(exclude={"native_root", "checkpoint"})
        self.info = ActorInfo(
            model=config.model,
            runtime=config.runtime,
            model_id=hashlib.sha256(json.dumps(model_identity, sort_keys=True).encode()).hexdigest(),
            checkpoint_sha256=checkpoint_sha256,
            profile=config.profile,
            model_config_name=config.model_config_name,
            policy_rng_seed=config.policy_rng_seed,
        )

    def create_session(self):
        return self.runtime.create_session()

    def close_session(self, state):
        self.runtime.close_session(state)

    def predict_batch(self, requests):
        return [result if isinstance(result, Exception) else NumericChunk(actions=result)
                for result in self.runtime.predict_batch(requests)]

    def close(self):
        self.runtime.close()
