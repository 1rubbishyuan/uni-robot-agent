"""Send SSE keepalive pings while the gateway buffers a Messages exchange."""
import asyncio
import json
import math

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from uni_robot_agent.provider.recording.exchanges import response_error_detail


class BufferedMessagesResponse(Response):
    def __init__(self, request, forward, interval_s):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Messages keepalive interval must be positive finite seconds")
        super().__init__(media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        self.request, self.forward, self.interval_s = request, forward, interval_s

    async def __call__(self, scope, receive, send):
        pending = asyncio.create_task(run_in_threadpool(self.forward))
        started, delivery_error = False, None
        try:
            # Fast replies keep their original HTTP status and headers.
            while not pending.done():
                await asyncio.wait({pending}, timeout=self.interval_s)
                if pending.done():
                    break
                if await self.request.is_disconnected():
                    raise ConnectionResetError("Client disconnected while awaiting provider response")
                if not started:
                    await send({"type": "http.response.start", "status": 200,
                                "headers": [(b"content-type", b"text/event-stream"),
                                            (b"cache-control", b"no-cache")]})
                    started = True
                await send({"type": "http.response.body", "body": b'event: ping\ndata: {"type":"ping"}\n\n',
                            "more_body": True})
        except (Exception, asyncio.CancelledError) as error:
            delivery_error = error

        # A synchronous upstream worker cannot be cancelled here. Retain its owner
        # until the normal response path can revoke bindings and finish the trace.
        with anyio.CancelScope(shield=True):
            response = await asyncio.shield(pending)
            if started and (not 200 <= response.status_code < 300
                            or "text/event-stream" not in response.headers.get("content-type", "")):
                response.body = _error_event(response.status_code, response.body)
                response.complete = False
                if response.exchange is not None:
                    response.exchange["client_response"] = response.body

            async def deliver(message):
                if delivery_error is not None:
                    raise delivery_error
                if started and message["type"] == "http.response.start":
                    return
                await send(message)

            # Reuse the sole binding/trace finalizer; pings never authorize tools.
            await response(scope, receive, deliver)


def _error_event(status, body):
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("type") != "error" or not isinstance(payload.get("error"), dict):
        kind = {401: "authentication_error", 403: "permission_error", 429: "rate_limit_error",
                529: "overloaded_error"}.get(status, "api_error")
        message = response_error_detail(body) or f"Provider returned HTTP {status} without a Messages stream"
        payload = {"type": "error", "error": {"type": kind, "message": message}}
    return ("event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()
