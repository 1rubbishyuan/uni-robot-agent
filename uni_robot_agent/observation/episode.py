"""Observation tools over published episode snapshots, independent of motion."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path

from uni_robot_agent.context.prompt import PromptContribution, get_prompts_dir
from uni_robot_agent.execution.scheduler import ToolExecutionPolicy
from uni_robot_agent.observation.hub import ObservationData, ObservationHub
from uni_robot_agent.tools.observation import build_observation_tools
from uni_robot_agent.tools.toolkit import ImageAttachment, ToolDef


class SnapshotObservationProvider:
    """Read immutable remote images through the session store without rendering."""

    def __init__(self, client, *, mirror):
        self.client = client
        self.mirror = mirror

    def capture(self) -> ObservationData:
        snapshot = self.client.latest_snapshot()
        paths = {}
        for camera, frame in snapshot["cameras"].items():
            ref = frame["rgb"]
            paths[camera] = str(self.mirror.local_path(ref))
        return ObservationData(
            source=self.client.info.integration,
            state=snapshot["state"], image_paths=paths,
            terminated=snapshot["terminated"], truncated=snapshot["truncated"],
            source_episode_id=snapshot["episode_id"],
            source_snapshot_id=snapshot["snapshot_id"],
            source_state_version=snapshot["state_version"],
            source_captured_at=snapshot["captured_at"],
            source_current_state_version=snapshot.get("source_current_state_version"),
        )


@dataclass(frozen=True)
class ObservationProfile:
    hub: ObservationHub
    tools: tuple[ToolDef, ...]
    policies: dict[str, ToolExecutionPolicy]
    extra_guides: tuple[Path, ...] = ()

    @property
    def listeners(self):
        return (self.hub,)

    @property
    def prompt_contributions(self):
        directory = get_prompts_dir() / "observer"
        guides = (directory / "main_agent.md", *self.extra_guides)
        contributions = tuple(PromptContribution(user=guide.read_text(), source=str(guide)) for guide in guides)
        observer = directory / "observer.md"
        delegation = (
            "Observation subagent instructions to forward when delegating:\n"
            "The main agent must retain its role and physical-control responsibilities. "
            "The following instructions apply only to the observation subagent; "
            "include them with its concrete assignment.\n\n"
            "<observation_subagent_instructions>\n"
            + observer.read_text()
            + "</observation_subagent_instructions>"
        )
        return contributions + (PromptContribution(user=delegation, source=str(observer)),)

    def close(self):
        self.hub.close()


def _public_reference(snapshot, root):
    return {**{key: value for key, value in snapshot.items() if key != "image_paths"},
            "state_path": str(Path(snapshot["state_path"]).relative_to(root)),
            "image_cameras": list(snapshot["image_paths"])}


def _attach_images(handler, root):
    @wraps(handler)
    def invoke(*args, **kwargs):
        output = handler(*args, **kwargs)
        snapshot = output.extra.get("observation")
        if snapshot:
            for name, path in snapshot["image_paths"].items():
                output.images.append(ImageAttachment(data=Path(path).read_bytes(), name=name, mime_type="image/png"))
            output.extra["observation"] = _public_reference(snapshot, root)
        for item in output.extra.get("observations", []):
            item["observation"] = _public_reference(item["observation"], root)
        return output
    return invoke


def create_observation_profile(episode, session_dir, *, extra_guides=()) -> ObservationProfile:
    hub = ObservationHub(SnapshotObservationProvider(
        episode.env,
        mirror=episode.artifact_mirror,
    ), session_dir)
    image_tools = {"capture_observation", "get_latest_observation", "get_observation", "list_observations"}
    tools = tuple(
        replace(tool, handler=_attach_images(tool.handler, Path(session_dir).resolve())) if tool.name in image_tools else tool
        for tool in build_observation_tools(hub)
    )
    return ObservationProfile(hub, tools, {
        "capture_observation": ToolExecutionPolicy(
            resource_key="observation", return_mode="immediate", concurrency="parallel",
        ),
    }, extra_guides=tuple(extra_guides))
