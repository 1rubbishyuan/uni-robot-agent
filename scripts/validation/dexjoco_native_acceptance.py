"""Native DexJoCo acceptance checks; requires the isolated simulator runtime."""
import argparse
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from uni_robot_agent.artifacts.store import ArtifactStore
from uni_robot_agent.integrations.dexjoco.environment.native_compat import (
    CONTROL_DT,
    HAND_JOINTS,
    create_native,
    simulation_time,
)
from uni_robot_agent.integrations.dexjoco.environment.server import prepare
from uni_robot_agent.services.environment.contracts import (
    FinishRequest,
    Pose,
    RequestIdentity,
    RequestRejected,
)
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedDeviceStep,
    DeviceEpisodeInfo,
    DeviceRequest,
)
from uni_robot_agent.services.environment.owner import OwnedAdapter
from uni_robot_agent.services.environment.service import (
    EnvironmentService,
    ServiceError,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prepared = SimpleNamespace(native_root=args.native_root, task="pick_bucket",
                               seed=args.seed, run_id="dexjoco-native-acceptance", depth=False)
    adapter = OwnedAdapter(lambda: prepare(prepared))
    artifacts = ArtifactStore(args.output_dir / "artifacts")
    instruction, metadata = adapter.episode_identity()
    info = DeviceEpisodeInfo(episode_id=prepared.run_id, task=instruction, integration="dexjoco",
                            environment=adapter.description, metadata=metadata)
    service = EnvironmentService(adapter, info, artifacts)
    report = {"seed": args.seed, "native_revision": metadata["native_revision"],
              "reset_sha256": metadata["reset_sha256"]}
    try:
        initial = service.initialize()
        serialized = json.dumps({"snapshot": initial, "info": info.model_dump(mode="json")})
        assert not any(word in serialized for word in (
            "bucket_ori_pose", "boxed_food_ori_pose", "table_delta_height", '"succeed"', '"task_success"'))
        assert set(initial["cameras"]) == {"front", "wrist"}
        assert all(item["image_size"] == [640, 640] for item in initial["cameras"].values())
        assert all(np.asarray(item["intrinsic_K"]).shape == (3, 3)
                   and np.asarray(item["T_world_from_optical"]).shape == (4, 4)
                   for item in initial["cameras"].values())
        state = adapter.read_state()
        pose = Pose.model_validate(state.devices["right_arm"]["control_pose"])
        hand = next(item for item in adapter.description.devices if item.name == "right_hand")
        joints = {name: float(np.clip(state.devices["right_hand"]["joint_positions_rad"][name], low, high))
                  for name, (low, high) in zip(HAND_JOINTS, hand.metadata["actuator_target_limits"], strict=True)}
        command = CoordinatedDeviceStep(device="robot", poses={"right_arm": pose},
                                         joints={"right_hand": joints})

        def identity(name):
            return RequestIdentity(episode_id=prepared.run_id, request_id=name,
                                   sequence=service.info().next_sequence)

        cancelled = threading.Event()
        cancelled.set()
        feedback = adapter.execute_device(command, cancellation=cancelled)
        assert feedback["steps_executed"] == 0 and adapter.read_state().state_version == 0
        bad = command.model_copy(deep=True)
        del bad.joints["right_hand"]["thj3"]
        rejected_id = identity("incomplete-hand")
        try:
            service.device_step(rejected_id, DeviceRequest(command=bad, expected_state_version=0))
            raise AssertionError("Incomplete hand command was accepted")
        except RequestRejected:
            assert adapter.read_state().state_version == 0

        step_id = identity("synchronized-step")
        request = DeviceRequest(command=command, expected_state_version=0)
        result = service.device_step(step_id, request)
        assert result["state_after"]["state_version"] == 1
        assert result["state_after"]["completed_commands"] == 1
        assert result["feedback"]["steps_executed"] == 1
        assert np.isclose(result["feedback"]["duration_s"], CONTROL_DT)
        assert service.request_status(step_id.request_id)["status"] == "completed"
        assert "succeed" not in json.dumps(result) and "task_success" not in json.dumps(result)
        try:
            service.device_step(step_id, request)
            raise AssertionError("Duplicate command was replayed")
        except ServiceError as exc:
            assert exc.code == "duplicate_request"
        try:
            service.device_step(identity("stale-step"), request)
            raise AssertionError("Stale command was accepted")
        except RequestRejected:
            assert adapter.read_state().state_version == 1
        measured = result["state_after"]["devices"]
        actual_pose = measured["right_arm"]["control_pose"]
        actual = np.array([*actual_pose["position"], *actual_pose["quaternion"],
                           *(measured["right_hand"]["joint_positions_rad"][name] for name in HAND_JOINTS)])
        outcome = service.finish(identity("finish"), FinishRequest(reason="native_acceptance"))["outcome"]
        report.update(filtered_state=True, cameras=list(initial["cameras"]), synchronized_steps=1,
                      simulation_duration_s=result["feedback"]["duration_s"],
                      invalid_and_stale_rejected=True, duplicate_not_replayed=True,
                      cancelled_without_step=True, official_task_success=outcome["task_success"])
    finally:
        service.close()

    # Compare the same seeded state/action directly through the official environment.
    native, _, fingerprint = create_native(args.native_root, seed=args.seed)
    try:
        assert fingerprint == metadata["reset_sha256"]
        action = np.asarray([*pose.position, *pose.quaternion, *(joints[n] for n in HAND_JOINTS)])
        before = simulation_time(native)
        observation, _, _, _, _ = native.step(action)
        np.testing.assert_allclose(actual, np.asarray(observation["state"])[:23], rtol=0, atol=1e-10)
        assert np.isclose(simulation_time(native) - before, CONTROL_DT)
        report.update(repeated_seed_identical=True, direct_native_step_matches=True)
    finally:
        native.close()
    (args.output_dir / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
