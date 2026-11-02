"""Native camera APIs; RGB, metric depth and calibration from the same frozen scene."""
import numpy as np
from .native_worker import array
from .geometry import CameraCapture, optical_calibration
from uni_robot_agent.services.environment.snapshots import CameraFrame


def sapien_frame(camera, depth):
    camera.capture()
    obs = camera.get_obs(rgb=True, depth=depth, position=False, segmentation=False)
    params = camera.get_params()
    # ManiSkill 3.0.1 shader transforms publish depth in millimetres.
    rgb = array(obs["rgb"])[0, ..., :3].astype(np.uint8)
    z = array(obs["depth"])[0, ..., 0].astype(np.float32)/1000 if depth else None
    k = array(params["intrinsic_cv"])[0]
    extrinsic = array(params["extrinsic_cv"])[0]
    world_to_optical = np.eye(4)
    world_to_optical[:3, :4] = extrinsic[:3, :4]
    return CameraFrame(rgb, z, k, np.linalg.inv(world_to_optical),
                       {"status": "valid", "source": "sapien_camera"})


def capture(worker, names, depth):
    n, family = worker.native, worker.family
    if family == "robocasa":
        if worker.camera_capture is None:
            # Match native visibility: collision meshes must not occlude visual RGBD.
            worker.camera_capture = CameraCapture(n.env.sim.model._model, n.env.sim.data._data,
                {name: name for name in n.env.sim.model.camera_names}, depth_enabled=depth,
                scene_option=n.env.sim._render_context_offscreen.vopt)
        worker.camera_capture.depth_enabled = depth
        return worker.camera_capture.capture(names)
    frames = {}
    if family in {"maniskill_cpu", "maniskill_gpu"}:
        n.u.scene.update_render(update_sensors=True, update_human_render_cameras=True)
        cameras = {**n.u._sensors, **n.u._human_render_cameras}
        for name in names:
            frames[name] = sapien_frame(cameras[name], depth)
        return frames
    if family == "genesis":
        for name in names:
            camera = n.cams[name]
            rgb, z, _, _ = camera.render(rgb=True, depth=depth)
            frames[name] = CameraFrame(array(rgb).astype(np.uint8), array(z).astype(np.float32) if depth else None,
                np.asarray(camera.intrinsics), np.linalg.inv(camera.extrinsics),
                {"status": "valid", "source": "genesis_camera"})
        return frames
    # Native Factory defaults to a MuJoCo visualization of the parts. Its model
    # origin is translated to the fixed part; calibration must undo that shift.
    import mujoco
    for name in names:
        rgb = n._mj_render(name).copy()
        camera = mujoco.mjv_averageCamera(n.mjr.scene.camera[0], n.mjr.scene.camera[1])
        k, transform = optical_calibration(camera, rgb.shape[1], rgb.shape[0])
        transform[:3, 3] += n._np(n.u.fixed_pos)
        z = None
        if depth:
            n.mjr.enable_depth_rendering()
            try:
                z = n.mjr.render().copy()
            finally:
                n.mjr.disable_depth_rendering()
        frames[name] = CameraFrame(rgb, z, k, transform,
            {"status": "valid", "source": "factory_mujoco_proxy", "scene_is_proxy": True})
    return frames
