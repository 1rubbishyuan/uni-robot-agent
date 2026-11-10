"""Model-only result views for clean manipulation; execution records stay intact."""
from copy import deepcopy

from uni_robot_agent.context.prompt import PromptContribution
from uni_robot_agent.tools.gripper.pose import quaternion_to_rpy
from uni_robot_agent.tools.presentation import round_physical


RESULT_NOTES = (PromptContribution(system=(
    "Clean result format: call_ok describes call validity, not object success. "
    "Tool schemas define parameters, defaults, limits and returned evidence. "
    "Read each arm's control_frame and grasp_site_description in the environment description. "
    "steps_executed counts accepted control commands; executed_native_steps counts environment steps. "
    "state.arms[name].pose is measured world [x,y,z,roll,pitch,yaw], metres and fixed-axis XYZ radians. "
    "state.native_steps, state.ended and remaining_steps describe episode progress. "
    "gripper is measured normalized opening; gripper_command is the retained drive intent. "
    "Metric gripper_opening_m and gripper_target_opening_m are measured gap and retained target. "
    "A distinct applied command is reported when native slew limiting changes it. "
    "To preserve grip, omit the gripper argument; copying measured opening changes the command. "
    "The measured pose describes the registered grasp site. Pose arrival does not establish object attachment. "
    "snapshot_id identifies the returned RGB observation. An artifact_error means observation delivery failed, "
    "not that acknowledged motion failed; do not repeat an action to recover its images. "
    "feedback preserves arrival/opening outcomes, errors, tolerances, steps and stop reason. "
    "Recovery, rejected/uncertain motion and publication failures retain their evidence; do not replay motion to fetch it. "
    "observation.image_cameras identifies image attachments in order. observation.cameras contains their "
    "image_path (a directly readable image file), image_size [height,width], intrinsic_K and "
    "T_world_from_optical when available. Image paths remain available after old inline images leave context. "
    "Each matrix describes that captured image, not a future view. Camera/physical values are rounded to "
    "at most five decimals for display; observation.artifact_ref is snapshot JSON with original calibration, "
    "not an image. Read images from observation.cameras[name].image_path; snapshot artifact IDs are not filenames. "
    "Missing matrices do not imply known geometry. Static robot resources and initial poses remain in initial input."
), source="tools/clean"),)


def _pose6(pose):
    return round_physical([*pose["position"], *quaternion_to_rpy(pose["quaternion"]).tolist()])


def state_view(state, *, initial=False):
    result = deepcopy(state)
    for arm in result.get("arms", {}).values():
        arm["pose"] = _pose6(arm.pop("control_pose"))
        metadata = arm.pop("metadata", {})
        for key in ("gripper_opening_m", "gripper_target_opening_m"):
            if key in metadata:
                arm[key] = metadata.pop(key)
        for key, equivalent in (("gripper_applied_command", arm.get("gripper_command")),
                                ("gripper_applied_target_opening_m", arm.get("gripper_target_opening_m"))):
            if key in metadata and metadata[key] != equivalent:
                arm[key] = metadata[key]
            metadata.pop(key, None)
        # This contract only publishes T_world_from_root for a static robot base.
        if not initial:
            metadata.pop("T_world_from_root", None)
        if "T_world_from_root" in metadata:
            metadata["T_world_from_root"] = round_physical(metadata["T_world_from_root"])
        if metadata:
            arm["metadata"] = metadata
        for key in ("joints", "gripper", "gripper_command", "gripper_opening_m",
                    "gripper_target_opening_m", "gripper_applied_command", "gripper_applied_target_opening_m"):
            if key in arm:
                arm[key] = round_physical(arm[key])
    metadata = result.get("metadata", {})
    metadata.pop("instruction", None)
    metadata.pop("remaining_steps", None)
    if not metadata:
        result.pop("metadata", None)
    # Observation provenance owns its version; execution counters remain in state.
    result.pop("state_version", None)
    return result


def feedback_view(feedback):
    if (feedback.get("goal_recovery") or feedback.get("stop_evidence")
            or feedback.get("no_execution") or feedback.get("targets_reached") is False
            or (feedback.get("ik_statistics") or {}).get("ik_request_failures", 0)):
        return deepcopy(feedback)
    result = deepcopy(feedback)
    for key in ("goal_recovery", "ik_statistics", "control_frames", "profile",
                "requested_max_steps", "requested_steps", "action", "stop_evidence", "no_execution"):
        result.pop(key, None)
    # Keep aggregate pose arrival: public tool descriptions explicitly name it.
    for arm in result.get("arms", {}).values():
        for key in ("target_pose", "actual_pose", "target_position", "target_quaternion",
                    "actual_position", "actual_quaternion", "execution_target_position",
                    "execution_target_quaternion", "target_gripper", "actual_gripper"):
            arm.pop(key, None)
        if arm.get("opening_reached") is None:
            arm.pop("opening_reached", None)
    if not result.get("arms"):
        result.pop("arms", None)
    return result


def observation_view(payload):
    cameras = {}
    for name, original in payload.get("camera_calibration", {}).items():
        camera = deepcopy(original)
        for key in ("intrinsic_K", "T_world_from_optical"):
            if camera.get(key) is None:
                camera.pop(key, None)
            else:
                camera[key] = round_physical(camera[key])
        # Unsupported/invalid calibration remains explicit; only intentional RGB-only is quiet.
        if camera.get("calibration", {}).get("status") == "disabled":
            camera.pop("calibration")
        cameras[name] = camera
    return {"state_version": payload.get("image_state_version", payload.get("state_version")),
            "captured_at": payload.get("captured_at"),
            "artifact_ref": payload.get("observation_path"),
            "cameras": cameras, "image_cameras": list(payload.get("image_cameras", cameras))}


def result_view(payload, *, initial=False):
    """Preserve errors and unrecognized evidence; omit only documented repetition."""
    if payload.get("call_ok") is False or payload.get("error"):
        return deepcopy(payload)
    result = deepcopy(payload)
    if "state" in payload:
        result["state"] = state_view(payload["state"], initial=initial)
        if "remaining_steps" not in result:
            remaining = payload["state"].get("metadata", {}).get("remaining_steps")
            if remaining is not None:
                result["remaining_steps"] = remaining
    if "feedback" in payload:
        result["feedback"] = feedback_view(payload["feedback"])
    if "camera_calibration" in payload:
        result["observation"] = observation_view(payload)
        for key in ("camera_calibration", "image_state_version", "state_version", "captured_at",
                    "observation_path", "image_cameras", "cameras"):
            result.pop(key, None)
    for key in ("robot_resources", "robot_calibration_path", "robot_calibration_sha256", "robot_manual"):
        result.pop(key, None)
    return result
