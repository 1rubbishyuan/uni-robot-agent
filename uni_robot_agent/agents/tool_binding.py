"""Correlate delivered model calls with exactly one executor submission."""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass
from uuid import uuid4

from uni_robot_agent.events import AgentActivity, ToolCallBatch, ToolCall
from uni_robot_agent.execution.correlation import call_context
from uni_robot_agent.provider.contracts import PreparedReply, RequestContext
from uni_robot_agent.services.mcp.results import encode_call_error, encode_call_result


@dataclass
class DeliveredCall:
    name: str
    arguments: dict
    exchange_id: str
    previous: str | None
    delivered: bool = False
    revoked: bool = False
    claimed: bool = False
    acknowledged: bool = False


class DeliveredToolBinding:
    """Keep SDK identity and delivery ordering outside the resource scheduler.

    Claims are not persisted across process restarts. A restarted run must never
    replay uncertain physical work using this in-memory ledger.
    """

    def __init__(self, read_calls, *, event_bus=None, timeout_s=60):
        self.read_calls = read_calls
        self.bus = event_bus
        self.timeout_s = timeout_s
        self.thread_id = uuid4().hex
        self.calls: dict[str, DeliveredCall] = {}
        self.routes: dict[str, str] = {}
        self.condition = threading.Condition()
        self.cancelled = False
        self.active = 0
        self.finish_submitted = False
        self.tool_calls = 0
        self.exchanges = 0
        self.max_exchanges = 0
        self.limit_reached = False

    def configure_tools(self, schemas, prefixes):
        self.specs = {item['name']: item for item in schemas}
        self.routes = {prefix + name: name for prefix in prefixes for name in self.specs}

    def request_context(self, request):
        with self.condition:
            if self.cancelled:
                raise RuntimeError('Agent session is cancelled')
            if self.max_exchanges and self.exchanges >= self.max_exchanges:
                self.limit_reached = True
                raise RuntimeError('Agent model exchange limit reached')
        return RequestContext(metadata={'thread_id': self.thread_id})

    def prepare_response(self, body, exchange_id, metadata):
        calls = tuple(self.read_calls(body))
        with self.condition:
            ids = [item['call_id'] for item in calls]
            if any(not key or key in self.calls for key in ids) or len(ids) != len(set(ids)):
                raise ValueError('Model response has missing or reused call IDs')
            if any(not isinstance(item['arguments'], dict) for item in calls):
                raise ValueError('Tool arguments must be an object')
            previous = None
            for item in calls:
                arguments = item['arguments']
                self.calls[item['call_id']] = DeliveredCall(
                    item['name'], copy.deepcopy(arguments), exchange_id, previous,
                )
                if item['name'] in self.routes:
                    previous = item['call_id']
        return PreparedReply(body, exchange_id, metadata, calls)

    def response_written(self, reply):
        with self.condition:
            self.exchanges += 1
            for item in reply.tool_calls:
                self.calls[item['call_id']].delivered = True
            if self.bus and reply.tool_calls:
                self.bus.emit(ToolCallBatch(batch_id=reply.exchange_id,
                    tool_names=tuple(c['name'] for c in reply.tool_calls), tool_calls=reply.tool_calls))
            self.condition.notify_all()

    def response_failed(self, reply):
        with self.condition:
            for item in reply.tool_calls:
                self.calls[item['call_id']].revoked = True
            self.condition.notify_all()

    def claim(self, call_id, name, arguments):
        deadline = time.monotonic() + self.timeout_s
        with self.condition:
            while True:
                call = self.calls.get(call_id)
                if self.cancelled or call is None or call.revoked:
                    raise RuntimeError('Tool call has no active model response')
                if self.routes.get(call.name) != name or call.arguments != arguments:
                    raise ValueError('Tool callback does not match its declared call ID')
                if call.claimed:
                    raise RuntimeError('Duplicate tool callback; execution will not be replayed')
                previous = self.calls.get(call.previous)
                if call.delivered and (previous is None or previous.acknowledged):
                    call.claimed = True
                    self.active += 1
                    self.tool_calls += 1
                    return call
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Tool response delivery or batch acknowledgement timed out')
                self.condition.wait(remaining)

    def dispatch(self, executor, params):
        """Consume identity before schema validation; it is never model input."""
        claimed = None
        try:
            if not isinstance(params.get('arguments', {}), dict) or not isinstance(params.get('_meta', {}), dict):
                raise ValueError('Tool arguments and metadata must be objects')
            arguments = dict(params.get('arguments', {}))
            metadata = params.get('_meta', {})
            identity = arguments.pop('_uni_robot_agent_call', None) or metadata.get('uni_robot_agent/call_id')
            name = params.get('name')
            if not isinstance(identity, str) or name not in self.specs:
                raise ValueError('Robot tool callback requires an explicit call ID')
            claimed = self.claim(identity, name, arguments)
            import jsonschema
            jsonschema.validate(arguments, self.specs[name]['input_schema'])
            transport_id = 'mcp_' + uuid4().hex
            if self.bus:
                self.bus.emit(ToolCall(tool_name=name, arguments=arguments,
                    call_id=transport_id, batch_id=claimed.exchange_id))
                self.bus.emit(AgentActivity(kind='tool_started', agent_id=self.thread_id,
                    item_id=identity, transport_call_id=transport_id, exchange_id=claimed.exchange_id,
                    tool_names=(name,)))
            with call_context(transport_id, claimed.exchange_id):
                result = executor.call(name, arguments)
                # A queued finish is not a completed submission. Wait for its receipt.
                if name == 'finish' and result.job_id and result.output is None:
                    result = executor.job_result(result.job_id, timeout=self.timeout_s)
            encoded = encode_call_result(result)
            if name == 'finish' and result.output is not None:
                output = result.output
                self.finish_submitted = bool(output.success and not output.error
                                             and output.extra.get('submitted') is True)
            encoded['_meta'] = {'uni_robot_agent/call_id': transport_id}
            if self.bus:
                self.bus.emit(AgentActivity(kind='tool_failed' if encoded['isError'] else 'tool_completed',
                    agent_id=self.thread_id, item_id=identity, transport_call_id=transport_id,
                    exchange_id=claimed.exchange_id, tool_names=(name,)))
            return encoded
        except Exception as error:
            return encode_call_error(error)
        finally:
            if claimed is not None:
                with self.condition:
                    claimed.acknowledged = True
                    self.active -= 1
                    self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.cancelled = True
            self.condition.notify_all()

    def drain(self, timeout=15):
        with self.condition:
            if not self.condition.wait_for(lambda: self.active == 0, timeout):
                raise RuntimeError('Robot tool callback remains active during cleanup')


def responses_calls(body):
    from uni_robot_agent.provider.protocols.sse import decode_events
    decoded = decode_events(body)
    if decoded is None:
        payload = json.loads(body)
    else:
        payload = next((e['response'] for e in decoded[0] if e.get('type') == 'response.completed'), None)
    if payload is None or payload.get('status') != 'completed':
        raise ValueError('Responses exchange did not complete')
    return [dict(name=i['name'], call_id=i['call_id'], arguments=json.loads(i['arguments']))
            for i in payload.get('output', []) if i.get('type') == 'function_call']
