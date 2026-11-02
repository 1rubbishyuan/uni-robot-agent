"""Explicit uni-robot-agent visibility selection, independent of execution and feature modes."""

from collections.abc import Iterable
from dataclasses import dataclass

from uni_robot_agent.tools.toolkit import ToolDef

TOOL_GROUPS = {
    "@skills": ("list_skills", "read_skill"),
    "@scene": ("view_env_state", "capture_scene", "locate"),
    "@motion": ("move", "grasp", "release", "hold"),
    "@workspace": ("read_text_file", "write_text_file", "list_dir"),
    "@observation": ("capture_observation", "get_latest_observation", "get_observation",
                     "list_observations", "publish_insight", "get_latest_insight", "read_insight_updates"),
    "@jobs": ("get_tool_result", "wait_for_tool", "wait_for_tools", "list_tool_jobs",
              "cancel_tool", "queue_status", "flush_queue"),
}


@dataclass(frozen=True)
class ToolSelection:
    requested: tuple[str, ...]
    expanded: tuple[str, ...]
    tools: tuple[ToolDef, ...]
    control_names: tuple[str, ...]

    def record(self, schemas: list[dict]) -> dict:
        """Record the actual executor surface, including selected job controls."""
        return {"requested": list(self.requested), "expanded": list(self.expanded),
                "visible": [spec["name"] for spec in schemas], "schemas": schemas}


def select_tools(declared_tools: Iterable[ToolDef], selectors: Iterable[str]) -> ToolSelection:
    """Expand explicit groups/names; never infer dependencies or enable services."""
    catalog = {}
    for tool in declared_tools:
        if tool.name in catalog:
            raise ValueError(f"Duplicate tool declaration: {tool.name}")
        catalog[tool.name] = tool
    controls = set(TOOL_GROUPS["@jobs"])
    if overlap := controls.intersection(catalog):
        raise ValueError(f"Tool names reserved by runtime controls: {sorted(overlap)}")
    requested = tuple(selectors)
    expanded = {}
    for selector in requested:
        if selector.startswith("@") and selector not in TOOL_GROUPS:
            raise ValueError(f"Unknown tool group: {selector}")
        for name in TOOL_GROUPS.get(selector, (selector,)):
            if name not in catalog and name not in controls:
                raise ValueError(f"Unknown tool: {name} (selector {selector})")
            expanded[name] = None
    return ToolSelection(
        requested=requested, expanded=tuple(expanded),
        tools=tuple(catalog[name] for name in expanded if name in catalog),
        control_names=tuple(name for name in expanded if name in controls),
    )
