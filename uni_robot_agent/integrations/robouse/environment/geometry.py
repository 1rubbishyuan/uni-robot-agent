"""Capture RGB and optional metric depth on one owner thread, without physics."""
import numpy as np
from uni_robot_agent.services.environment.snapshots import CameraFrame


def optical_calibration(camera, width, height):
    """Use the actual renderer camera, including free cameras and stereo averaging."""
    forward = np.asarray(camera.forward, dtype=float)
    up = np.asarray(camera.up, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up); right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack((right, down, forward))
    transform[:3, 3] = camera.pos
    near = float(camera.frustum_near)
    top, bottom = float(camera.frustum_top), float(camera.frustum_bottom)
    fy = height * near / (top - bottom)
    # Renderer uses square pixels; frustum_center is the horizontal near-plane offset.
    fx = fy
    cx = width / 2 - float(camera.frustum_center) * fx / near
    cy = height * top / (top - bottom)
    return np.array([[fx, 0., cx], [0., fy, cy], [0., 0., 1.]]), transform


class CameraCapture:
    def __init__(self, model, data, cameras, *, depth_enabled, size=320, scene_option=None):
        self.model, self.data, self.cameras = model, data, dict(cameras)
        self.depth_enabled, self.size = depth_enabled, size
        self.scene_option = scene_option
        self.renderer = None

    def capture(self, names):
        import mujoco
        if self.renderer is None:
            # Some native backends allocate only a 256px framebuffer.
            self.model.vis.global_.offwidth = max(self.model.vis.global_.offwidth, self.size)
            self.model.vis.global_.offheight = max(self.model.vis.global_.offheight, self.size)
            self.renderer = mujoco.Renderer(self.model, height=self.size, width=self.size)
        frames = {}
        for name in names:
            self.renderer.disable_depth_rendering()
            self.renderer.update_scene(self.data, camera=self.cameras[name], scene_option=self.scene_option)
            rgb = self.renderer.render().copy()
            camera = mujoco.mjv_averageCamera(self.renderer.scene.camera[0], self.renderer.scene.camera[1])
            k, transform = optical_calibration(camera, self.size, self.size)
            depth = None
            if self.depth_enabled:
                self.renderer.enable_depth_rendering()
                try:
                    depth = self.renderer.render().copy()
                finally:
                    self.renderer.disable_depth_rendering()
            frames[name] = CameraFrame(rgb, depth, k, transform, {"status": "valid", "source": "mujoco_render_camera"})
        return frames

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None


def resolve_cameras(backend, kind, model, names):
    """Resolve explicit script choices. Free viewpoints retain upstream camera setup."""
    result = {}
    for name in names:
        if kind == "kitchen" and name == "front":
            result[name] = backend.record_camera()
        elif kind == "gymrobotics" and name == "default":
            backend.render()
            result[name] = backend._cam
        else:
            model.camera(name)  # Reject unknown names before starting the service.
            result[name] = name
    return result
