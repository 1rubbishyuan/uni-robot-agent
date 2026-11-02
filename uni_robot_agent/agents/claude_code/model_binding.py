"""Associate delivered model batches with exact Claude tool-use identities."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import uuid4

from uni_robot_agent.events import AgentActivity, ToolCallBatch
from uni_robot_agent.provider.contracts import PreparedReply, RequestContext
from uni_robot_agent.provider.protocols.messages import message_response


@dataclass
class Call:
    name: str
    arguments: dict
    exchange_id: str
    previous: str | None
    delivered: bool = False
    revoked: bool = False
    claimed: bool = False
    finished: bool = False
    agent_id: str = ""
    result_reported: bool = False


class ClaudeModelBinding:
    def __init__(self, event_bus=None):
        self.thread_id = str(uuid4())
        self.bus = event_bus
        self.calls: dict[str, Call] = {}
        self.messages: set[str] = set()
        self.lock = threading.RLock()
        self.cancelled = False
        self.tool_names = None
        self.batches: dict[str, PreparedReply] = {}
        self.observed_batches: set[str] = set()
        self.child_agents: dict[str, str] = {}
        self.agent_parents: dict[str, str] = {}

    def request_context(self, request):
        # Messages requests do not carry a reliable child identity. Attribute
        # tool exchanges from SDK hook IDs instead of labelling all requests main.
        return RequestContext(metadata={"thread_id": self.thread_id, "thread_source": "claude_session"})

    def prepare_response(self, body, exchange_id, metadata):
        message = message_response(body)
        calls = tuple({"call_id": block["id"], "name": block["name"], "arguments": block["input"]}
                      for block in message.get("content", []) if block.get("type") == "tool_use")
        with self.lock:
            if any(call["call_id"] in self.calls for call in calls):
                raise ValueError("Claude reused a tool-use ID across model exchanges")
            previous = None
            for call in calls:
                self.calls[call["call_id"]] = Call(call["name"], call["arguments"], exchange_id, previous)
                if self.tool_names is None or call["name"] in self.tool_names:
                    previous = call["call_id"]
        return PreparedReply(body, exchange_id, {**metadata, "turn_id": message.get("id", exchange_id)}, calls)

    def response_written(self, reply):
        with self.lock:
            self.messages.add(reply.metadata["turn_id"])
            self.batches[reply.exchange_id] = reply
            for call in reply.tool_calls:
                self.calls[call["call_id"]].delivered = True

    def observe_call(self, call_id, agent_id):
        """Use the SDK's explicit owner, never timing or argument matching."""
        with self.lock:
            call = self.calls.get(call_id)
            if self.cancelled or call is None or call.revoked:
                raise RuntimeError("Tool call has no active model exchange")
            # The SDK can receive the bytes before the gateway's delivery callback.
            # Do not allow native tools or MCP callbacks until delivery is confirmed.
            if not call.delivered:
                return False
            reply = self.batches.get(call.exchange_id)
            first = call.exchange_id not in self.observed_batches
            self.observed_batches.add(call.exchange_id)
            for item in reply.tool_calls:
                candidate = self.calls[item["call_id"]]
                if candidate.agent_id and candidate.agent_id != agent_id:
                    raise ValueError("One Claude model response has conflicting tool owners")
                candidate.agent_id = agent_id
            parent = self.agent_parents.get(agent_id, "")
            if self.bus:
                if first:
                    names = tuple(item["name"] for item in reply.tool_calls)
                    self.bus.emit(AgentActivity(kind="tool_batch", agent_id=agent_id,
                        parent_agent_id=parent, turn_id=reply.metadata["turn_id"],
                        exchange_id=reply.exchange_id, tool_names=names, tool_calls=reply.tool_calls))
                    if agent_id == self.thread_id:
                        self.bus.emit(ToolCallBatch(batch_id=reply.exchange_id, tool_names=names, tool_calls=reply.tool_calls))
                self.bus.emit(AgentActivity(kind="tool_started", agent_id=agent_id,
                    parent_agent_id=parent, item_id=call_id, exchange_id=call.exchange_id,
                    tool_names=(call.name,), tool_calls=({"call_id": call_id,
                        "name": call.name, "arguments": call.arguments},)))
            return True

    def register_subagent(self, agent_id, parent_call_id):
        """Link the SDK task ID to its exact spawning tool, including nested agents."""
        with self.lock:
            call = self.calls.get(parent_call_id)
            if call is None or call.name not in {"Agent", "Task"} or not call.agent_id:
                return
            self.child_agents[parent_call_id] = agent_id
            self.agent_parents[agent_id] = call.agent_id
            if self.bus:
                self.bus.emit(AgentActivity(kind="started", agent_id=agent_id,
                    parent_agent_id=call.agent_id, item_id=parent_call_id, label="subagent"))

    def child_identity(self, parent_call_id):
        with self.lock:
            call = self.calls.get(parent_call_id)
            return self.child_agents.get(parent_call_id, ""), call.agent_id if call else ""

    def tool_result(self, call_id, *, failed):
        with self.lock:
            call = self.calls.get(call_id)
            if call is None:
                return
            # An SDK waiter ending does not stop an already claimed robot action.
            if not call.claimed:
                call.finished = True
            if self.bus and not call.result_reported:
                self.bus.emit(AgentActivity(kind="tool_failed" if failed else "tool_completed",
                    agent_id=call.agent_id,
                    parent_agent_id=self.agent_parents.get(call.agent_id, ""),
                    item_id=call_id, exchange_id=call.exchange_id, tool_names=(call.name,),
                    tool_calls=({"call_id": call_id, "name": call.name, "arguments": call.arguments},)))
            call.result_reported = True

    def response_failed(self, reply):
        with self.lock:
            for call in reply.tool_calls:
                self.calls[call["call_id"]].revoked = True

    def claim(self, call_id, name, arguments):
        """Claim once, after delivery and preceding calls have acknowledged execution."""
        with self.lock:
            call = self.calls.get(call_id)
            if self.cancelled or call is None or call.revoked:
                raise RuntimeError("Tool call has no active, delivered model exchange")
            if call.name != name or call.arguments != arguments:
                raise ValueError("Tool callback differs from its model tool-use ID")
            if name == "mcp__uni_robot_agent__finish" and call.agent_id != self.thread_id:
                call.finished = True
                raise PermissionError("Only the main agent may finish the episode; return findings to the parent")
            if call.claimed:
                raise RuntimeError("Duplicate Claude tool callback")
            previous = self.calls.get(call.previous)
            if not call.delivered or previous is not None and not previous.finished:
                return None
            call.claimed = True
            return call

    def finish(self, call_id):
        with self.lock:
            self.calls[call_id].finished = True

    def cancel(self):
        with self.lock:
            self.cancelled = True
