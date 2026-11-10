"""Reproduce NaVILA's episode-history sampling and native JPEG input."""
from PIL import Image

from uni_robot_agent.services.actor.images import decode_image


def prepare_history(frames):
    if not 1 <= len(frames) <= 128:
        raise ValueError("NaVILA requires between one and 128 real episode frames")
    identifiers = [frame.frame_id for frame in frames]
    times = [frame.simulation_time_s for frame in frames]
    if len(set(identifiers)) != len(identifiers) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("NaVILA history must contain unique frames in increasing simulation time")
    padding = max(0, 8 - len(frames))
    padded = [None] * padding + list(frames)
    count = len(padded)
    indices = [int(i * (count - 1) / 7) for i in range(7)] + [count - 1]
    size = decode_image(frames[-1].image).shape[:2]
    images, evidence = [], []
    for index in indices:
        frame = padded[index]
        if frame is None:
            image = Image.new("RGB", (size[1], size[0]), (0, 0, 0))
            evidence.append({"kind": "black_padding"})
        else:
            pixels = decode_image(frame.image)
            if pixels.shape[:2] != size:
                raise ValueError("NaVILA episode frames must use one image geometry")
            image = Image.fromarray(pixels)
            evidence.append({"kind": "observed", "frame_id": frame.frame_id,
                             "simulation_time_s": frame.simulation_time_s,
                             "state_version": frame.state_version})
        images.append(image)
    return images, {"input_frames": evidence, "history_frame_count": len(frames),
                    "sampling": "episode_uniform_8_front_black_padding", "jpeg_quality": 75}
