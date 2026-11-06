"""Serve JSON-RPC and SSE tool calls for Codex and other HTTP MCP clients."""

from __future__ import annotations

import json
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from uni_robot_agent.execution import ToolExecutor
from uni_robot_agent.services.hosting import HttpServer, create_app
from uni_robot_agent.services.mcp.results import encode_call_error, encode_call_result
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("mcp.http")


class HttpMcpServer:
    """MCP adapter: tool requests require IDs; only initialized is a notification."""

    def __init__(
        self, executor: ToolExecutor, *, host: str = "127.0.0.1", port: int = 0,
        event_bus: Any = None, call_handler=None, auth_token: str | None = None,
    ) -> None:
        self._executor = executor
        self._event_bus = event_bus
        self._host, self._port = host, port
        self._call_handler = call_handler
        self._auth_token = auth_token
        self._server: HttpServer | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            return f"http://{self._host}:{self._port}/mcp/"
        return self._server.url + "/mcp/"

    def _dispatch(self, body: dict[str, Any]) -> dict[str, Any]:
        method, params = body["method"], body.get("params", {})
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "uni_robot_agent", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [
                {"name": spec["name"], "description": spec.get("description", ""),
                 "inputSchema": spec.get("input_schema", {"type": "object"})}
                for spec in self._executor.get_tool_schemas()
            ]}
        elif method == "tools/call":
            result = self._call_tool(params)
        else:
            return self._error(body.get("id"), -32601, f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": code, "message": message}}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._call_handler is not None:
            return self._call_handler(params)
        name, arguments = params.get("name", ""), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return {"content": [{"type": "text", "text": "Invalid tool name or arguments"}],
                    "isError": True}
        transport_call_id = "mcp_" + uuid4().hex
        if self._event_bus:
            from uni_robot_agent.events import ToolCall as ToolCallEvent

            # Toolkit emits its result synchronously; publish the call before execution.
            self._event_bus.emit(ToolCallEvent(
                tool_name=name, arguments=arguments, call_id=transport_call_id,
                source_step=self._executor.tool_execution_seq + 1,
            ))
        try:
            from uni_robot_agent.execution.correlation import call_context
            with call_context(transport_call_id):
                result = self._executor.call(name, arguments)
            encoded = encode_call_result(result)
        except Exception as error:  # noqa: BLE001 - tool errors are MCP results.
            encoded = encode_call_error(error)
        # MCP metadata is returned to the SDK, outside model-visible tool content.
        encoded["_meta"] = {"uni_robot_agent/call_id": transport_call_id}
        return encoded

    def _router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/mcp/")
        async def mcp(request: Request):
            if self._auth_token is not None:
                import hmac
                if not hmac.compare_digest(request.headers.get('authorization', ''),
                                           'Bearer ' + self._auth_token):
                    return Response(status_code=401)
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                return JSONResponse(self._error(None, -32700, "Parse error"), status_code=400)
            if (not isinstance(body, dict) or body.get("jsonrpc") != "2.0"
                    or not isinstance(body.get("method"), str)
                    or not isinstance(body.get("params", {}), dict)):
                return JSONResponse(self._error(None, -32600, "Invalid request"), status_code=400)
            if "id" not in body:
                if body["method"] == "notifications/initialized":
                    return Response(status_code=202)
                # This adapter does not offer generic JSON-RPC fire-and-forget tools.
                # Reject at the HTTP boundary, without a JSON-RPC notification reply.
                return Response(
                    "Only notifications/initialized may omit an id; MCP requests require an id",
                    status_code=400, media_type="text/plain",
                )
            if body["id"] is None or isinstance(body["id"], bool) or not isinstance(body["id"], (str, int)):
                return JSONResponse(self._error(None, -32600, "Invalid request id"), status_code=400)
            if (body["method"] == "tools/call"
                    and "text/event-stream" in request.headers.get("accept", "")):
                async def stream():
                    # StreamingResponse sends headers before entering this generator. Codex
                    # releases its next concurrent request only after receiving those headers.
                    result = await run_in_threadpool(self._dispatch, body)
                    yield f"event: message\ndata: {json.dumps(result)}\n\n"

                return StreamingResponse(stream(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache"})
            return JSONResponse(await run_in_threadpool(self._dispatch, body))

        return router

    def start(self) -> str:
        if self._server is not None:
            raise RuntimeError("MCP HTTP server is already started")
        self._server = HttpServer(create_app(self._router()), self._host, self._port)
        self._server.start_background()
        logger.info("HttpMcpServer ready at %s", self.url)
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
