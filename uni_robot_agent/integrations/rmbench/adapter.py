"""Keep RMBench's native memory transitions and third-view observation intact."""
import numpy as np

from uni_robot_agent.integrations.robotwin.adapter import CAMERAS, RoboTwinAdapter


class RMBenchAdapter(RoboTwinAdapter):
    def __init__(self, env, **kwargs):
        super().__init__(env, cameras=(*CAMERAS, "third_view"), **kwargs)
        self.description.metadata["observation_source"] = "native_rmbench_four_views"

    def observe(self):
        # Only the native action loop calls check_success: it also advances memory
        # phases. Repeated public captures reuse this cached observation.
        observation = self.env.get_obs()
        camera = self.env.cameras.third_view_camera
        item = {"rgb": observation["third_view_rgb"],
                "intrinsic_cv": camera.get_intrinsic_matrix(),
                "extrinsic_cv": camera.get_extrinsic_matrix()}
        if self.depth_enabled:
            # SAPIEN Position is metres; the shared adapter consumes millimetres.
            item["depth"] = -np.asarray(camera.get_picture("Position"))[..., 2] * 1000
        observation["observation"]["third_view"] = item
        return observation
