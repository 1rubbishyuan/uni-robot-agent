"""Native OpenPI prediction with session-local noise and one batching queue."""
import dataclasses

import numpy as np

from uni_robot_agent.services.actor.images import decode_image

from .native_compat import load_policy


@dataclasses.dataclass
class _Session:
    index: int = 0
    rng: object = None


def _sample_noise(session, *, seed, horizon, latent_dim):
    import jax

    if session.rng is None:
        session.rng = jax.random.key(seed)
    session.rng, sample_rng = jax.random.split(session.rng)
    # Preserve X5's leading singleton, split order and per-episode sequence under batching.
    return np.asarray(jax.random.normal(sample_rng, (1, horizon, latent_dim)))[0]


class OpenpiJaxRuntime:
    def __init__(self, settings):
        loaded = load_policy(settings)
        self.settings = settings
        self.policy, self.identity = loaded.policy, loaded.identity
        self.horizon, self.latent_dim = loaded.action_horizon, loaded.latent_action_dim

    def create_session(self):
        return _Session()

    def close_session(self, state):
        state.rng = None

    def close(self):
        self.policy = None

    def _prepare_observation(self, observation):
        settings = self.settings
        state = np.asarray(observation.state)
        if state.shape != (len(settings.profile.observation_fields),) or not np.isfinite(state).all():
            raise ValueError("Expected finite state matching the actor profile")
        if not isinstance(observation.instruction, str):
            raise TypeError("Expected one prompt string per request")
        if set(observation.images) != set(settings.profile.cameras):
            raise ValueError("Image keys differ from the actor profile")
        images = {}
        for camera, image in observation.images.items():
            image = decode_image(image)
            if settings.resize is not None:
                from openpi_client.image_tools import resize_with_pad

                image = resize_with_pad(image, *settings.resize)
            if settings.image_layout == "CHW":
                image = image.transpose(2, 0, 1)
            images[settings.image_keys[camera]] = image
        if settings.observation_mapping is None:
            result = {"state": state}
        else:
            indices = {field.name: index for index, field in enumerate(settings.profile.observation_fields)}
            result = {key: state[[indices[name] for name in names]]
                      for key, names in settings.observation_mapping.items()}
        result[settings.prompt_key] = observation.instruction
        if settings.image_container:
            result[settings.image_container] = images
        else:
            result.update(images)
        return result

    def _batch_observations(self, observations):
        def stack(values):
            if isinstance(values[0], dict):
                return {key: stack([value[key] for value in values]) for key in values[0]}
            if isinstance(values[0], str):
                return values
            return np.stack(values)

        return stack(observations)

    def predict_batch(self, requests):
        results = [None] * len(requests)
        if self.settings.inference_mode == "single" and len(requests) > 1:
            return [ValueError("This pinned OpenPI API requires max_batch_size=1") for _ in requests]
        valid = []
        for index, work in enumerate(requests):
            try:
                observation = self._prepare_observation(work.observation)
                noise = _sample_noise(work.state, seed=self.settings.policy_rng_seed,
                                      horizon=self.horizon, latent_dim=self.latent_dim)
                valid.append((index, work.state, observation, noise))
            except Exception as exc:  # noqa: BLE001 - malformed requests must not affect other sessions.
                results[index] = exc
        if not valid:
            return results
        try:
            if self.settings.inference_mode == "native":
                observations = self._batch_observations([item[2] for item in valid])
                noise = np.stack([item[3] for item in valid])
                output = self.policy.infer(observations, noise=noise)
                actions = np.asarray(output["actions"], dtype=np.float32)
            else:
                output = self.policy.infer(valid[0][2], noise=valid[0][3])
                actions = np.asarray(output["actions"], dtype=np.float32)[None, ...]
            expected = (len(valid), self.horizon, len(self.settings.profile.action_fields))
            if actions.shape != expected:
                raise RuntimeError(f"Expected OpenPI actions shaped {expected}, received {actions.shape}")
            for row, (index, session, _, _) in enumerate(valid):
                if not np.isfinite(actions[row]).all():
                    results[index] = RuntimeError("Expected finite OpenPI actions")
                    continue
                results[index] = actions[row].tolist()
                session.index += 1
        except Exception as exc:  # noqa: BLE001 - one failed model call fails every included request.
            for index, *_ in valid:
                results[index] = exc
        return results
