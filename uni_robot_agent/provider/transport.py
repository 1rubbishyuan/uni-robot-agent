"""Cancellable model HTTP requests behind the synchronous provider boundary."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
import threading
from urllib.parse import urlsplit

import httpx

from uni_robot_agent.services.errors import HttpCallError
from uni_robot_agent.telemetry import NullTracer


class ModelRequestCancelled(RuntimeError):
    def __init__(self, message, *, response=None):
        super().__init__(message)
        self.response = response


class ModelTransport:
    """Own one HTTP event loop; cancellation waits for actual request cleanup."""

    def __init__(self, *, timeout_s, tracer=None):
        self._timeout = httpx.Timeout(timeout_s, connect=10, pool=10)
        self._tracer = tracer or NullTracer()
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._ready = threading.Event()
        self._cancelled = threading.Event()
        self._loop = None
        self._thread = None
        self._client = None
        self._client_closed = False
        self._startup_error = None
        self._tasks = set()
        self._closed = False
        self._shutdown_future = None
        self._stop_requested = False

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, trust_env=True,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
            )
        except BaseException as error:
            self._startup_error = error
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _request(self, method, url, options):
        if self._cancelled.is_set():
            raise asyncio.CancelledError
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            # HTTPX buffers the complete body and rejects truncated responses.
            return await self._client.request(method, url, **options)
        finally:
            self._tasks.remove(task)

    def request(self, method, url, *, content, headers, check=True):
        outgoing = dict(headers)
        parsed = urlsplit(url)
        trace_url = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1], query="", fragment="").geturl()
        with self._tracer.span("rpc.client", method=method, url=trace_url) as span:
            if self._tracer.enabled:
                context = span.context
                outgoing = {key: value for key, value in outgoing.items() if key.lower() != "traceparent"}
                outgoing["traceparent"] = f"00-{context.trace_id}-{context.span_id}-01"
            with self._lock:
                if self._cancelled.is_set():
                    raise ModelRequestCancelled("Model request cancelled")
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="provider-http", daemon=True)
                    self._thread.start()
                    self._ready.wait()
                if self._startup_error is not None:
                    raise RuntimeError("Model HTTP transport failed to start") from self._startup_error
                future = asyncio.run_coroutine_threadsafe(
                    self._request(method, url, {"content": content, "headers": outgoing}), self._loop,
                )
            try:
                response = future.result()
            except CancelledError as error:
                raise ModelRequestCancelled("Model request cancelled") from error
            if self._cancelled.is_set():
                raise ModelRequestCancelled("Model request cancelled", response=response)
            if check and not 200 <= response.status_code < 300:
                raise HttpCallError(response)
            return response

    def _cancel_active(self):
        for task in tuple(self._tasks):
            if not task.cancelling():
                task.cancel()

    def cancel(self):
        with self._lock:
            self._cancelled.set()
            if self._loop is not None and not self._loop.is_closed():
                # Cancelling a concurrent Future would return before the coroutine
                # releases its socket. Cancel the actual tasks on their owner loop.
                self._loop.call_soon_threadsafe(self._cancel_active)

    async def _shutdown(self):
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()
        self._client_closed = True
        await self._loop.shutdown_asyncgens()
        await self._loop.shutdown_default_executor()

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self.cancel()
            if self._thread is not None:
                if self._startup_error is None:
                    if self._client_closed and self._shutdown_future is not None and self._shutdown_future.done():
                        if self._shutdown_future.cancelled() or self._shutdown_future.exception() is not None:
                            # Retry only after client close completed successfully.
                            # HTTPX may mark itself closed before transport cleanup
                            # fails, making a later aclose silently skip that work.
                            self._shutdown_future = None
                    if self._shutdown_future is None:
                        self._shutdown_future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
                    # A timed-out close retains this future; a retry must wait for
                    # the same cleanup, never launch another concurrent shutdown.
                    self._shutdown_future.result(timeout=5)
                    if not self._stop_requested:
                        self._loop.call_soon_threadsafe(self._loop.stop)
                        self._stop_requested = True
                self._thread.join(timeout=5)
                if self._thread.is_alive():
                    raise TimeoutError("Model HTTP transport cleanup is still running")
            self._closed = True
