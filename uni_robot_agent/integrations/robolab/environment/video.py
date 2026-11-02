"""Host-only recording of the sensor frames returned by native physics steps."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


class EpisodeVideo:
    def __init__(self, directory, dt):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = shutil.which("ffmpeg")
        if self.ffmpeg is None:
            raise RuntimeError("Episode video recording requires ffmpeg")
        self.dt = float(dt)
        self.process = None
        self.log = None
        self.frames = 0
        self.cameras = None
        self.size = None

    def add(self, observation):
        images = observation.get("image_obs", {})
        if not images:
            return
        if self.cameras is None:
            self.cameras = list(images)
        panels = []
        for name in self.cameras:
            pixels = images[name]
            if hasattr(pixels, "detach"):
                pixels = pixels.detach().cpu().numpy()
            panels.append(np.asarray(pixels)[0, :, :, :3].astype(np.uint8))
        height = max(panel.shape[0] for panel in panels)
        panels = [np.asarray(Image.fromarray(panel).resize((round(panel.shape[1] * height / panel.shape[0]), height)))
                  if panel.shape[0] != height else panel for panel in panels]
        frame = np.concatenate(panels, axis=1)
        # H.264's yuv420p output requires even dimensions.
        frame = np.pad(frame, ((0, frame.shape[0] % 2), (0, frame.shape[1] % 2), (0, 0)))
        size = (frame.shape[1], frame.shape[0])
        if self.process is None:
            self.size = size
            self.log = (self.directory / "video.ffmpeg.log").open("wb")
            self.process = subprocess.Popen([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{size[0]}x{size[1]}",
                "-framerate", str(1 / self.dt), "-i", "pipe:0", "-an", "-c:v", "libx264",
                "-threads", "1", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(self.directory / "episode.mp4"),
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log)
        if size != self.size:
            raise ValueError("Camera dimensions changed during episode video recording")
        self.process.stdin.write(np.ascontiguousarray(frame).tobytes())
        self.frames += 1

    def close(self):
        if self.process is None:
            return
        process, self.process = self.process, None
        try:
            process.stdin.close()
            code = process.wait(timeout=60)
            if code:
                raise RuntimeError(f"Video encoder failed with exit code {code}; see video.ffmpeg.log")
            (self.directory / "video.json").write_text(json.dumps({
                "file": "episode.mp4", "frames": self.frames, "fps": 1 / self.dt,
                "duration_s": self.frames * self.dt, "width": self.size[0], "height": self.size[1],
                "cameras_left_to_right": self.cameras, "time_basis": "simulation control steps",
            }, indent=2))
        finally:
            self.log.close()
