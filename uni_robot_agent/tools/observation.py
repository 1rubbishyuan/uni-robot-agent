"""Declarative tools backed by an ``ObservationHub``."""

from __future__ import annotations

from uni_robot_agent.observation.hub import ObservationHub
from uni_robot_agent.tools.toolkit import (
    ToolDef, ToolOutput, ToolProvider, ToolSpec, bind_tools, schema,
)


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec("capture_observation",
            "Read and persist a consistent published environment observation, including during motion.",
            schema({}), readonly=True),
        ToolSpec("get_latest_observation",
            "Read the latest shared observation and its freshness and world version.",
            schema({}), readonly=True),
        ToolSpec("get_observation",
            "Read a historical environment observation by observation ID.",
            schema({"observation_id": {"type": "string"}}, required=("observation_id",)),
            readonly=True),
        ToolSpec("list_observations",
            "List environment observations after a given sequence number.",
            schema({
                "after_seq": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 20},
            }), readonly=True),
        ToolSpec("publish_insight",
            "Publish a structured insight linked to an observation and world version.",
            schema({
                "observer_id": {"type": "string"},
                "observation_id": {"type": "string"},
                "world_version": {"type": "integer"},
                "facts": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "summary": {"type": "string"},
            }, required=("observer_id", "observation_id", "world_version", "facts"))),
        ToolSpec("get_latest_insight",
            "Read the latest insight published by an observer.",
            schema({"observer_id": {"type": "string"}}, required=("observer_id",)), readonly=True),
        ToolSpec("read_insight_updates",
            "Read new observer insights after a cursor.",
            schema({
                "observer_id": {"type": "string"},
                "after_seq": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 20},
            }, required=("observer_id",)), readonly=True),
    ]


class ObservationTools(ToolProvider):
    """Expose lightweight observation and insight operations as agent tools."""

    provider_id = "observation"

    def __init__(self, hub: ObservationHub | None) -> None:
        self._hub = hub

    def get_tools(self) -> list[ToolDef]:
        return bind_tools(
            tool_specs(),
            provider_id=self.provider_id,
            invoke=self._invoke,
            handlers={
                "capture_observation": self.capture_observation,
                "get_latest_observation": self.get_latest_observation,
                "get_observation": self.get_observation,
                "list_observations": self.list_observations,
                "publish_insight": self.publish_insight,
                "get_latest_insight": self.get_latest_insight,
                "read_insight_updates": self.read_insight_updates,
            },
        )

    def _invoke(self, handler, **arguments):
        if self._hub is None:
            return ToolOutput(success=False, error="Observation profile is disabled",
                              extra={"no_execution": True})
        return handler(**arguments)

    def capture_observation(self) -> ToolOutput:
        return ToolOutput(extra=self._hub.capture_observation())

    def get_latest_observation(self) -> ToolOutput:
        return ToolOutput(extra=self._hub.latest_observation())

    def get_observation(self, observation_id: str) -> ToolOutput:
        try:
            return ToolOutput(extra=self._hub.get_observation(observation_id))
        except KeyError as exc:
            return ToolOutput(success=False, error=str(exc))

    def list_observations(self, after_seq: int = 0, limit: int = 20) -> ToolOutput:
        return ToolOutput(extra={
            "observations": self._hub.list_observations(
                after_seq=after_seq,
                limit=limit,
            ),
        })

    def publish_insight(
        self,
        observer_id: str,
        observation_id: str,
        world_version: int,
        facts: list[str],
        changes: list[str] | tuple[str, ...] = (),
        risks: list[str] | tuple[str, ...] = (),
        confidence: float | None = None,
        summary: str | None = None,
    ) -> ToolOutput:
        try:
            return ToolOutput(extra={
                "insight": self._hub.publish_insight(
                    observer_id=observer_id,
                    observation_id=observation_id,
                    world_version=world_version,
                    facts=facts,
                    changes=changes,
                    risks=risks,
                    confidence=confidence,
                    summary=summary,
                ),
            })
        except (TypeError, ValueError) as exc:
            return ToolOutput(success=False, error=str(exc))

    def get_latest_insight(self, observer_id: str) -> ToolOutput:
        return ToolOutput(extra={
            "insight": self._hub.latest_insight(observer_id),
        })

    def read_insight_updates(
        self,
        observer_id: str,
        after_seq: int = 0,
        limit: int = 20,
    ) -> ToolOutput:
        insights = self._hub.read_insight_updates(
            observer_id,
            after_seq=after_seq,
            limit=limit,
        )
        next_cursor = insights[-1]["insight_seq"] if insights else after_seq
        return ToolOutput(extra={
            "insights": insights,
            "next_cursor": next_cursor,
        })


def build_observation_tools(hub: ObservationHub) -> list[ToolDef]:
    """Build observation tools in their stable public order."""
    return ObservationTools(hub).get_tools()
