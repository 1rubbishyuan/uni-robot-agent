"""Claude SDK messages projected into uni-robot-agent events and terminal state."""
import json
from uni_robot_agent.events import AssistantText, ToolCall, ReasoningSummary, AgentActivity


def get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def kind(value):
    return str(value.get("type") or value.get("kind") or "") if isinstance(value, dict) else type(value).__name__


def finish_succeeded(block):
    if get(block, "is_error", False):
        return False
    for content in get(block, "content", []) or []:
        if get(content, "type") != "text":
            continue
        try:
            value = json.loads(get(content, "text", ""))
        except ValueError:
            continue
        if isinstance(value, dict) and "call_ok" in value:
            return value["call_ok"] is True and not value.get("error")
    return False


class ClaudeEvents:
    def __init__(self, bus, binding):
        self.bus, self.binding = bus, binding
        self.turns = self.tool_calls = self.steps = 0
        self.texts = []
        self.seen_messages = set()
        self.seen_calls = set()
        self.finish_calls = {}
        self.finish_request = None
        self.completed = False
        self.error = self.stop_reason = None
        self.session_id = None

    def emit(self, event):
        if self.bus:
            self.bus.emit(event)

    def consume(self, message):
        message_kind = kind(message)
        parent_call = get(message, "parent_tool_use_id")
        if parent_call:
            # Tool hooks carry the authoritative child ID. Keep child prose
            # separate from the main agent's completion and accounting.
            agent_id, parent_agent_id = self.binding.child_identity(parent_call)
            for block in get(message, "content", []) or []:
                if kind(block) == "TextBlock" and get(block, "text"):
                    self.emit(AgentActivity(kind="message", item_id=parent_call,
                        agent_id=agent_id, parent_agent_id=parent_agent_id,
                        label="subagent", text=get(block, "text")))
            return
        # TaskStartedMessage is a SystemMessage subclass with a distinct class name.
        if get(message, "subtype") == "task_started":
            data = get(message, "data", {})
            agent_id = get(message, "task_id") or data.get("task_id")
            parent_call = get(message, "tool_use_id") or data.get("tool_use_id")
            if agent_id and parent_call:
                self.binding.register_subagent(agent_id, parent_call)
            return
        if message_kind == "SystemMessage":
            subtype = get(message, "subtype", "")
            if subtype in {"init", "compact_boundary"}:
                self.emit(AgentActivity(
                    kind="started" if subtype == "init" else "compacted",
                    agent_id=self.binding.thread_id, label="main",
                    text=json.dumps(get(message, "data", {}).get("compact_metadata", {})),
                ))
            return
        if message_kind == "AssistantMessage":
            identity = get(message, "message_id")
            if identity is None or identity not in self.seen_messages:
                self.turns += 1
                if identity:
                    self.seen_messages.add(identity)
            if get(message, "error"):
                self.error = str(get(message, "error"))
            for block in get(message, "content", []) or []:
                if kind(block) == "TextBlock" and get(block, "text"):
                    self.texts.append(get(block, "text"))
                    self.steps += 1
                    self.emit(AssistantText(text=get(block, "text"), agent_step=self.steps, turn=self.turns))
                elif kind(block) == "ThinkingBlock" and get(block, "thinking"):
                    self.emit(ReasoningSummary(text=get(block, "thinking")))
                elif kind(block) == "ToolUseBlock":
                    call_id = get(block, "id")
                    if call_id in self.seen_calls:
                        continue
                    self.seen_calls.add(call_id)
                    self.tool_calls += 1
                    self.steps += 1
                    name, arguments = get(block, "name", ""), get(block, "input", {})
                    self.emit(ToolCall(tool_name=name, arguments=arguments, source_step=self.steps, turn=self.turns))
                    if name in {"finish", "mcp__uni_robot_agent__finish"} and isinstance(arguments, dict):
                        self.finish_calls[call_id] = arguments
        elif message_kind == "UserMessage":
            for block in get(message, "content", []) or []:
                if kind(block) != "ToolResultBlock":
                    continue
                call_id = get(block, "tool_use_id")
                self.binding.tool_result(call_id, failed=get(block, "is_error", False))
                arguments = self.finish_calls.pop(call_id, None)
                if arguments is not None and finish_succeeded(block):
                    self.finish_request = arguments
        elif message_kind == "ResultMessage":
            self.session_id = get(message, "session_id")
            subtype, reason = get(message, "subtype", ""), get(message, "terminal_reason")
            self.completed = subtype == "success" and not get(message, "is_error", False) and reason in {None, "completed"}
            if subtype == "error_max_turns":
                self.stop_reason = "max_turns"
            elif not self.completed:
                if subtype == "error_max_budget_usd":
                    self.stop_reason = "budget_limit"
                details = get(message, "errors") or [get(message, "result") or reason or subtype]
                self.error = f"Claude Agent SDK ended ({subtype}): {'; '.join(str(x) for x in details)}"
