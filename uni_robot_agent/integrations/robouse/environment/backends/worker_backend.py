"""RoboUse backend entry point; preserve Episode grading with isolated native workers."""
from pathlib import Path
from robouse.backends.base import Backend, ActionSpec, StepInfo
from robouse.workers.client import StdioWorker
from ..worker_frames import unpack_frames


class WorkerBackend(Backend):
    name = "uni_robot_agent_worker"
    uni_robot_agent_worker = True
    probe_success = False

    def __init__(self, spec):
        self.spec = spec
        self.family = spec["uni_robot_agent_family"]
        self.max_steps = int(spec["max_steps"])
        dim = 4 if self.family == "robocasa" else 6 if self.family == "factory" else 7
        names = ["dx", "dy", "dz"] + ([] if dim == 4 else ["rx", "ry", "rz"])
        if dim != 6:
            names += ["grip"]
        self.action_spec = ActionSpec(names, [-1.] * dim, [1.] * dim)
        self.camera = spec["uni_robot_agent_cameras"][0]
        self._grip = 0. if self.family in {"maniskill_cpu", "robocasa", "factory"} else 1.
        self._control = None
        temporary = Path(spec["uni_robot_agent_work_dir"]) / "native_tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        self.worker = StdioWorker("uni-robot-agent", Path(spec["uni_robot_agent_worker_python"]),
            Path(__file__).parents[1] / "native_worker.py", env={"PYTHONHASHSEED": "0", "TMPDIR": str(temporary),
                "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
            cwd=Path(spec["uni_robot_agent_work_dir"]), timeout=float(spec.get("rpc_timeout_s", 900)))

    def reset(self, seed):
        try:
            self.meta = self.worker.call("make", family=self.family, spec=self.spec, seed=int(seed))
        except BaseException:
            self.worker.close()
            raise
        self._control = None

    def control(self):
        if self._control is None:
            self._control = self.worker.call("control_state")
        return self._control

    def step(self, action):
        self._control = None
        result = self.worker.call("step", action=list(map(float, action)))
        if self.family != "factory":
            self._grip = float(action[-1])
        return StepInfo(success=bool(result["success"]))

    def observe(self):
        c = self.control()
        return {"hand_pos": c["position"], "hand_quat": c["quaternion"], "gripper_open": c["opening"]}

    def success(self):
        return bool(self.worker.call("success")["success"])

    def skills(self):
        return []

    def render(self, width=640, height=480):
        return self.worker.call("render", camera=self.camera, width=width, height=height)

    def capture(self, cameras, depth):
        return unpack_frames(self.worker.call("capture", cameras=cameras, depth=depth))

    def robot_model(self):
        return self.worker.call("robot_model")

    def close(self):
        self.worker.close()
