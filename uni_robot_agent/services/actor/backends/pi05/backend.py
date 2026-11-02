"""One Pi0.5 backend with explicit runtime and checkpoint selection."""

import hashlib
import json

from uni_robot_agent.services.actor.contracts import ActorInfo

from .config import Pi05Config, profile_for


class Pi05Backend:
    def __init__(self, config: Pi05Config):
        from .openpi_jax import OpenpiJaxRuntime

        self.runtime = OpenpiJaxRuntime(config)
        checkpoint_sha256 = self.runtime.identity["checkpoint_sha256"]
        profile = profile_for(config.profile)
        model_identity = {
            "checkpoint_sha256": checkpoint_sha256,
            "runtime": config.runtime,
            "model_config": config.model_config_name,
            "profile": profile.model_dump(),
        }
        self.info = ActorInfo(
            model="pi05",
            runtime=config.runtime,
            model_id=hashlib.sha256(
                json.dumps(model_identity, sort_keys=True).encode()
            ).hexdigest(),
            checkpoint_sha256=checkpoint_sha256,
            profile=profile,
            model_config_name=config.model_config_name,
            policy_rng_seed=0,
        )

    def create_session(self):
        return self.runtime.create_session()

    def close_session(self, state):
        self.runtime.close_session(state)

    def predict_batch(self, requests):
        return self.runtime.predict_batch(requests)

    def close(self):
        self.runtime.close()
