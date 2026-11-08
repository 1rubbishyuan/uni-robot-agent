"""CPU checks for integration composition, visibility and owned-resource cleanup.

Run from a checkout with PYTHONPATH=. python scripts/validation/tool_composition.py.
No robot, model or perception services are started. --snapshot PATH records the
legacy configuration's schemas and prompt text for cross-revision comparisons.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from uni_robot_agent import SessionConfig
from uni_robot_agent.integrations import load_integration
from uni_robot_agent.services.environment.contracts import (
    ArmDescription, CameraDescription, EnvironmentDescription, EpisodeInfo,
)
from uni_robot_agent.session import assemble_session
from uni_robot_agent.tools.hybrid.contracts import HybridCapabilities
from uni_robot_agent.tools.selection import TOOL_GROUPS
from uni_robot_agent.tools.toolkit import ToolOutput

INTEGRATIONS = ("robodojo", "robouse", "robolab", "robotwin", "libero_plus",
                "libero_pro", "rmbench", "calvin")
HYBRID = INTEGRATIONS[:3]
BASE_TOOLS = {"list_skills", "read_skill", "view_env_state", "capture_scene",
              "locate", "move", "grasp", "release", "hold", "finish"}
JOBS = set(TOOL_GROUPS["@jobs"])
WORKSPACE = set(TOOL_GROUPS["@workspace"])


class Environment:
    def __init__(self, integration):
        self.closed = False
        self.uncertain = False
        self.info = EpisodeInfo(episode_id="fixture", task="validation-task",
            integration=integration, metadata={"instruction_type": "default", "seed": 0},
            environment=EnvironmentDescription(
                arms=[ArmDescription(name="arm", control_frame="grasp",
                    grasp_site_description="Fixture grasp frame", rotation_radius_m=0.1)],
                cameras=[CameraDescription(name="front")],
                control_frequency_hz=20.0, max_episode_steps=100))
        self.initial = {"snapshot_id": "initial", "state_version": 0, "cameras": {},
            "state": {"arms": {"arm": {
                "control_pose": {"position": [0., 0., 0.], "quaternion": [1., 0., 0., 0.]},
                "joints": [], "gripper": 1., "gripper_command": 1.}},
                "state_version": 0, "native_steps": 0, "ended": False}}

    def connect(self):
        return self.info

    def attach_artifact_store(self, store):
        return SimpleNamespace()

    def record(self, step):
        return self.initial

    def close(self):
        self.closed = True


class Resource:
    def __init__(self, **values):
        self.closed = False
        self.__dict__.update(values)

    def close(self):
        self.closed = True


def config(integration="robouse", profile="direct", *, legacy=False, **settings):
    options = {"task": "validation-task"}
    if integration == "robodojo":
        options["include_extra_task_requirements"] = False
    if profile == "hybrid":
        options.update(vla_review=False, action_model_checkpoint_sha256="a" * 64)
        settings.setdefault("action_model_url", "http://actor.invalid")
    if legacy:
        options["action_profile"] = profile
    else:
        settings["tool_profile"] = profile
    return SessionConfig(integration=integration, env_url="http://environment.invalid",
                         no_sam3=True, integration_options=options, **settings)


@contextmanager
def assembled(configuration, directory, *, command="run", binding_error=False, calibrated=True):
    args = configuration.resolve()
    args.command, args._session_dir = command, str(directory)
    environment = Environment(args.integration)
    if not calibrated:
        environment.info.environment.cameras[0].has_depth = False
        environment.info.environment.cameras[0].has_calibration = False
    owned = []
    def actor(*args, **kwargs):
        resource = Resource(info=SimpleNamespace(model="fixture"))
        owned.append(resource)
        return resource
    def reviewer(**kwargs):
        resource = Resource(enabled=kwargs["enabled"])
        owned.append(resource)
        return resource
    binding = SimpleNamespace(
        capabilities=HybridCapabilities(groups=("arm",), constraint_schema={"type": "object"}),
        action_horizon=40, reviewer_supported=False, reviewer_prompt=None,
        validate_decision=lambda *a: None)
    assembly = None
    with ExitStack() as stack:
        stack.enter_context(patch("uni_robot_agent.integrations.prepared_episode.EnvironmentClient",
                                  return_value=environment))
        stack.enter_context(patch("uni_robot_agent.tools.gripper.provider.GripperTools.hold",
                                  return_value=ToolOutput(extra={"held": True})))
        actor_mock = stack.enter_context(patch("uni_robot_agent.integrations.hybrid.ActorClient",
                                              side_effect=actor))
        stack.enter_context(patch("uni_robot_agent.integrations.hybrid.ChunkReviewer", side_effect=reviewer))
        stack.enter_context(patch("uni_robot_agent.services.perception.client.PerceptionClient",
                                 side_effect=AssertionError("Unexpected perception connection")))
        if args.integration in HYBRID:
            stack.enter_context(patch(
                f"uni_robot_agent.integrations.{args.integration}.action_binding.create_binding",
                side_effect=ValueError("Fixture rejects binding") if binding_error else None,
                return_value=binding))
        try:
            assembly = assemble_session(args)
            yield assembly, environment, owned, actor_mock
        finally:
            if assembly is not None:
                assembly.executor.close()
                if assembly.observation_profile is not None:
                    assembly.observation_profile.close()
                assembly.integration_session.close()
                assembly.telemetry.close()
                assembly.artifacts.close()
            assert environment.closed, "Environment leaked"
            assert all(resource.closed for resource in owned), "Model resource leaked"


def surface(assembly):
    return {item["name"] for item in assembly.executor.get_tool_schemas()}


def snapshots(directory):
    records = {}
    for integration in INTEGRATIONS:
        for profile in (("direct", "hybrid") if integration in HYBRID else ("direct",)):
            for mode in ("immediate", "background"):
                for command in ("run", "tui"):
                    key = f"{integration}/{profile}/{mode}/{command}"
                    cfg = config(integration, profile, legacy=True, tool_return_mode=mode)
                    with assembled(cfg, directory, command=command) as (assembly, *_):
                        prompt = assembly.build_prompt("Fixture instruction")
                        records[key] = {"schemas": assembly.executor.get_tool_schemas(),
                                        "prompt": prompt.flatten()}
    return records


class CompositionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "session"

    def tearDown(self):
        self.temp.cleanup()

    def test_all_integrations_and_execution_modes(self):
        for integration in INTEGRATIONS:
            for profile in (("direct", "hybrid") if integration in HYBRID else ("direct",)):
                for mode in ("immediate", "background"):
                    for command in ("run", "tui"):
                        with self.subTest(integration=integration, profile=profile, mode=mode, command=command):
                            cfg = config(integration, profile, tool_return_mode=mode)
                            with assembled(cfg, self.directory, command=command) as (assembly, _, owned, actor):
                                expected = BASE_TOOLS | ({"actor_act"} if profile == "hybrid" else set())
                                expected |= JOBS if mode == "background" else set()
                                expected |= WORKSPACE if command == "tui" else set()
                                self.assertEqual(surface(assembly), expected)
                                self.assertEqual(actor.call_count, int(profile == "hybrid"))
                                self.assertEqual(len(owned), 2 if profile == "hybrid" else 0)
                                modules = assembly.integration_session.tool_providers
                                self.assertEqual("hybrid" in {p.provider_id for p in modules}, profile == "hybrid")
                                from uni_robot_agent.execution.code_as_policy.bridge import CodePolicyBridge
                                bridge = CodePolicyBridge(assembly.executor)
                                self.assertEqual(bridge.get_tool_schemas(), assembly.executor.get_tool_schemas())
                                bridge.close()
                                # Keep actual gripper policies and scheduling; replace only physical work.
                                result = assembly.executor.call("hold", {"steps": 1})
                                if mode == "immediate":
                                    self.assertEqual(result.status, "completed")
                                    self.assertTrue(result.output.success)
                                else:
                                    self.assertIsNotNone(result.job_id)
                                    self.assertTrue(assembly.executor.job_result(result.job_id, 5).output.success)
                                file_result = assembly.executor.call("write_text_file",
                                    {"path": str(self.directory / "probe.txt"), "content": "ok"})
                                self.assertEqual(file_result.output.success, command == "tui")

    def test_profile_validation_and_legacy_alias(self):
        for integration in INTEGRATIONS:
            self.assertEqual(config(integration, legacy=True).resolve().tool_profile, "direct")
            with self.assertRaisesRegex(ValueError, "does not support tool profile"):
                config(integration, "missing").resolve()
            if integration not in HYBRID:
                with self.assertRaisesRegex(ValueError, "does not support tool profile"):
                    config(integration, "hybrid").resolve()
        cfg = config()
        with self.assertRaisesRegex(ValueError, "select different"):
            cfg.model_copy(update={"integration_options": {"action_profile": "hybrid"}}).resolve()
        for missing in ("action_model_url", "action_model_checkpoint_sha256"):
            cfg = config(profile="hybrid")
            changes = {missing: None} if missing == "action_model_url" else {
                "integration_options": {**cfg.integration_options, missing: None}}
            with self.assertRaisesRegex(ValueError, "Hybrid requires"):
                cfg.model_copy(update=changes).resolve()

    def test_debug_visibility_and_disabled_features(self):
        for mode, selectors in (("background", ["finish"]), ("immediate", ["finish", "@jobs"]),
                                ("background", [])):
            cfg = config(tool_return_mode=mode, tool_debug=True, tools=selectors)
            with assembled(cfg, self.directory) as (assembly, *_):
                expected = ({"finish"} if selectors else set()) | (JOBS if "@jobs" in selectors else set())
                self.assertEqual(surface(assembly), expected)
                self.assertFalse(assembly.executor.call("list_skills", {}).output.success)
                self.assertEqual(json.loads((self.directory / "tool_selection.json").read_text())["visible"],
                                 [item["name"] for item in assembly.executor.get_tool_schemas()])
        cfg = config(tool_return_mode="immediate", tool_debug=True,
                     tools=["actor_act", "write_skill", "capture_observation"])
        with assembled(cfg, self.directory) as (assembly, _, _, actor):
            for name, arguments in (
                ("actor_act", {"instruction": "test", "active_groups": ["arm"]}),
                ("write_skill", {"path": "robouse/test.md", "content": "test"}),
                ("capture_observation", {}),
            ):
                self.assertFalse(assembly.executor.call(name, arguments).output.success)
            actor.assert_not_called()
        cfg = config(tool_debug=True, tools=["unknown"])
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            with assembled(cfg, self.directory):
                pass

    def test_debug_preserves_configured_prompt(self):
        for profile in ("direct", "hybrid"):
            with assembled(config(profile=profile), self.directory) as (assembly, *_):
                normal = assembly.build_prompt("Fixture").flatten()
            with assembled(config(profile=profile, tool_debug=True, tools=[]),
                           self.directory) as (assembly, *_):
                self.assertEqual(normal, assembly.build_prompt("Fixture").flatten())

    def test_cli_profile_and_alias(self):
        from uni_robot_agent.cli import build_parser
        from uni_robot_agent.config import resolve_settings
        for integration in INTEGRATIONS:
            spec = load_integration(integration).get_integration_spec()
            parser = build_parser(spec)
            for flag in ("--tool-profile", "--action-profile"):
                argv = ["run", "--integration", integration, "--model", "fixture",
                        "--task", "validation-task", "--env-url", "http://fixture.invalid",
                        "--no-sam3", flag, "direct"]
                args = resolve_settings(parser.parse_args(argv), spec, agent_enabled=False)
                self.assertEqual(args.tool_profile, "direct")
        self.assertFalse({"isaacsim", "mujoco", "torch"} & set(sys.modules))

    def test_observation_and_skill_writing_remain_optional(self):
        from uni_robot_agent.tools.observation import ObservationTools
        from uni_robot_agent.context.prompt import PromptContribution
        hub = SimpleNamespace(capture_observation=lambda: {"observation_id": "fixture"})
        profile = SimpleNamespace(tools=ObservationTools(hub).get_tools(), policies={},
            listeners=(), prompt_contributions=(PromptContribution(system="Fixture observer guidance"),),
            close=lambda: None)
        with patch("uni_robot_agent.session.load_observation_profile",
                   return_value=SimpleNamespace(create_observation_profile=lambda *a: profile)):
            cfg = config(observation_profile="robouse")
            with assembled(cfg, self.directory) as (assembly, *_):
                self.assertTrue(set(TOOL_GROUPS["@observation"]) <= surface(assembly))
                result = assembly.executor.call("capture_observation", {})
                self.assertTrue(result.output.success)
                self.assertIn("Fixture observer guidance", assembly.build_prompt("Observe").flatten())
        library = self.directory / "skill-library"
        library.mkdir(parents=True, exist_ok=True)
        with patch.dict("os.environ", {"UNI_ROBOT_AGENT_SKILLS_DIR": str(library)}):
            with assembled(config(skill_writing=True, tool_return_mode="immediate"),
                           self.directory) as (assembly, *_):
                result = assembly.executor.call("write_skill",
                    {"path": "robouse/learned.md", "content": "Fixture experience"})
                self.assertTrue(result.output.success)
                self.assertEqual((library / "robouse/learned.md").read_text(), "Fixture experience")

    def test_capabilities_and_failed_assembly_cleanup(self):
        with assembled(config(), self.directory, calibrated=False) as (assembly, *_):
            self.assertNotIn("locate", surface(assembly))
        with self.assertRaisesRegex(ValueError, "Fixture rejects binding"):
            with assembled(config(profile="hybrid"), self.directory, binding_error=True):
                pass

    def test_custom_composition_omits_motion_and_its_prompt(self):
        from uni_robot_agent.integrations.composition import resolve_tool_profile
        spec = load_integration("robouse").get_integration_spec()
        modules = tuple(m for m in spec.tool_profiles["direct"] if m.name != "gripper")
        spec = replace(spec, tool_profiles={"observe": modules}, default_tool_profile="observe")
        with patch("uni_robot_agent.integrations.robouse.get_integration_spec", return_value=spec):
            cfg = config(profile="observe")
            with assembled(cfg, self.directory) as (assembly, *_):
                self.assertFalse({"move", "grasp", "release", "hold"} & surface(assembly))
                self.assertNotIn("Gripper operation labels", assembly.build_prompt("Observe").flatten())
        duplicate = replace(spec, tool_profiles={"direct": (modules[0], modules[0])})
        with self.assertRaisesRegex(ValueError, "Duplicate tool module"):
            resolve_tool_profile(SimpleNamespace(tool_profile="direct"), duplicate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        with tempfile.TemporaryDirectory() as directory:
            records = snapshots(Path(directory))
        args.snapshot.write_text(json.dumps(records, indent=2) + "\n")
        print(f"Recorded {len(records)} schema/prompt combinations")
    else:
        unittest.main(argv=["tool_composition"], verbosity=2)


if __name__ == "__main__":
    main()
