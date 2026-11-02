"""Shared ASGI host. Native threads and model ownership remain with callers."""
import asyncio
import math
import signal
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from uni_robot_agent.telemetry import NullTracer, TraceContext

from .errors import ServiceError


class _RequestBoundary:
    def __init__(self, app, *, max_request_bytes, tracer):
        self.app, self.limit, self.tracer = app, max_request_bytes, tracer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"error": {"code": "invalid_length", "message": "Invalid Content-Length"}}, 400)(scope, receive, send)
        body = bytearray()
        if length < 0 or length > self.limit:
            return await self._too_large(scope, receive, send)
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                return await self._too_large(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        trace = None
        parts = headers.get(b"traceparent", b"").decode("ascii", errors="ignore").split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
            try:
                if int(parts[1], 16) and int(parts[2], 16):
                    trace = TraceContext(trace_id=parts[1], span_id=parts[2])
            except ValueError:
                pass
        with self.tracer.use_context(trace), self.tracer.span("rpc.server", method=scope["method"], path=scope["path"]):
            await self.app(scope, replay, send)

    async def _too_large(self, scope, receive, send):
        await JSONResponse({"error": {"code": "request_too_large", "message": "Request body exceeds service limit"}}, 413)(scope, receive, send)


def create_app(router, *, on_close=None, max_request_bytes=128 * 1024 * 1024, tracer=None):
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if on_close is not None:
                try:
                    await run_in_threadpool(on_close)
                except BaseException as error:
                    _app.state.shutdown_error = error
                    raise

    app = FastAPI(lifespan=lifespan, redirect_slashes=False)
    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError):
        return JSONResponse(error.payload(), status_code=error.status)

    app.add_middleware(_RequestBoundary, max_request_bytes=max_request_bytes, tracer=tracer or NullTracer())
    return app


class _Server(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.startup_aborted = False
        self._serve_loop = None
        self._serve_task = None

    @contextmanager
    def capture_signals(self):
        # Embedded simulators own their shutdown signals.
        yield

    async def serve(self, sockets=None):
        self._serve_loop = asyncio.get_running_loop()
        self._serve_task = asyncio.current_task()
        try:
            if self.startup_aborted:
                raise asyncio.CancelledError
            await super().serve(sockets=sockets)
        finally:
            self._serve_loop = self._serve_task = None

    def abort_startup(self):
        self.startup_aborted = self.should_exit = True
        loop, task = self._serve_loop, self._serve_task
        if loop is not None and task is not None:
            def cancel_if_starting():
                # If readiness won the race, preserve Uvicorn's graceful shutdown.
                if not self.started:
                    task.cancel()

            try:
                # Cancelling serve lets asyncio.run cancel and await lifespan cleanup.
                loop.call_soon_threadsafe(cancel_if_starting)
            except RuntimeError:
                # The serving loop can finish between reading it and scheduling.
                if not loop.is_closed():
                    raise


class HttpServer:
    def __init__(self, app, host="127.0.0.1", port=0):
        self.app = app
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((host, port))
            self.address = self.socket.getsockname()
            self.server_port = self.address[1]
            self._server = _Server(uvicorn.Config(app, host=host, port=port, log_level="warning",
                                                access_log=False, lifespan="on", proxy_headers=False))
        except BaseException:
            self.socket.close()
            raise
        self._thread = None
        self._failure = None
        self._start_lock = threading.Lock()
        self._start_claimed = False

    @property
    def url(self):
        return f"http://{self.address[0]}:{self.address[1]}"

    def start(self):
        self._claim_start()
        self._run()

    def _claim_start(self):
        with self._start_lock:
            if self._start_claimed or self.socket.fileno() == -1:
                raise RuntimeError("HTTP host cannot be started more than once or after close")
            self._start_claimed = True

    def _run(self):
        try:
            try:
                self._server.run(sockets=[self.socket])
            except asyncio.CancelledError:
                if not self._server.startup_aborted:
                    raise
            if not self._server.started and not self._server.startup_aborted:
                raise RuntimeError("HTTP host failed to start")
            error = getattr(getattr(self.app, "state", None), "shutdown_error", None)
            if error is not None:
                raise RuntimeError("HTTP service cleanup failed") from error
        except BaseException as error:
            self._failure = error
            raise
        finally:
            self.socket.close()

    def start_background(self, *, timeout_s=10, cleanup_timeout_s=5):
        """Wait for startup and bound cancellation cleanup if startup times out.

        Lifespan startup must be cancellable async code. Python cannot interrupt
        a callback blocking the event-loop thread; its socket is still released,
        and a timeout reports that the thread has not finished cleanup.
        """
        if not all(math.isfinite(value) and value > 0 for value in (timeout_s, cleanup_timeout_s)):
            raise ValueError("HTTP startup timeouts must be positive finite seconds")
        self._claim_start()

        def run():
            try:
                self._run()
            except BaseException as error:  # noqa: BLE001 - report failure to the host owner.
                # The owner observes background failures through stop.
                self._failure = error

        self._thread = threading.Thread(target=run, name="service-http", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout_s
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("HTTP host failed to start") from self._failure
            if time.monotonic() >= deadline:
                self._server.abort_startup()
                self._thread.join(cleanup_timeout_s)
                message = "HTTP host startup timed out"
                if self._thread.is_alive():
                    # Before readiness the owner still holds the bound socket.
                    # A ready host must close its listener on the serving loop.
                    if not self._server.started:
                        self.socket.close()
                    message += "; startup cleanup is still running"
                raise TimeoutError(message) from self._failure
            time.sleep(.005)
        return self.url

    def request_stop(self):
        """Stop accepting work; callers with native owner queues can keep pumping."""
        self._server.should_exit = True

    def stop(self, *, timeout_s=5):
        self.request_stop()
        with self._start_lock:
            if not self._start_claimed:
                self.socket.close()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                raise TimeoutError("HTTP host still has active requests")
        if self._failure is not None:
            raise RuntimeError("HTTP host failed") from self._failure


def serve(app, host="127.0.0.1", port=0):
    server = HttpServer(app, host, port)
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: server.request_stop())
    try:
        server.start()
    finally:
        try:
            server.stop()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
