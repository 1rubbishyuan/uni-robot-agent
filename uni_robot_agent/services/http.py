"""Shared synchronous HTTP transport with explicit timeouts and no replay."""
import math
from urllib.parse import urlsplit

import httpx

from uni_robot_agent.telemetry import NullTracer

from .errors import HttpCallError


class HttpTransport:
    def __init__(self, base_url="", *, timeout_s=450, connect_timeout_s=10,
                 tracer=None, trust_env=False, max_connections=32):
        if not all(math.isfinite(value) and value > 0 for value in (timeout_s, connect_timeout_s)):
            raise ValueError("HTTP timeouts must be positive finite seconds")
        self.connect_timeout_s = connect_timeout_s
        if base_url:
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("Service URL must be an absolute HTTP(S) base URL")
        self.base_url = base_url.rstrip("/")
        self.tracer = tracer or NullTracer()
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s, pool=connect_timeout_s),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
            follow_redirects=False, trust_env=trust_env,
        )

    def request(self, method, path, *, content=None, headers=None, check=True, timeout_s=None,
                data=None, files=None):
        url = path if path.startswith(("http://", "https://")) else self.base_url + "/" + path.lstrip("/")
        outgoing = dict(headers or {})
        parsed = urlsplit(url)
        trace_url = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1], query="", fragment="").geturl()
        with self.tracer.span("rpc.client", method=method, url=trace_url) as span:
            if self.tracer.enabled:
                context = span.context
                # Replace forwarded context with this hop's span, regardless of casing.
                outgoing = {key: value for key, value in outgoing.items() if key.lower() != "traceparent"}
                outgoing["traceparent"] = f"00-{context.trace_id}-{context.span_id}-01"
            options = {}
            if timeout_s is not None:
                if not math.isfinite(timeout_s) or timeout_s <= 0:
                    raise ValueError("HTTP timeout must be positive finite seconds")
                options["timeout"] = httpx.Timeout(
                    timeout_s, connect=min(timeout_s, self.connect_timeout_s),
                    pool=min(timeout_s, self.connect_timeout_s),
                )
            response = self.client.request(method, url, content=content, headers=outgoing,
                                           data=data, files=files, **options)
            # HTTPX reads the complete body before returning, detecting truncated responses.
            if check and not 200 <= response.status_code < 300:
                raise HttpCallError(response)
            return response

    def post_json(self, path, payload):
        import json
        return self.request("POST", path, content=json.dumps(payload, allow_nan=False).encode(),
                            headers={"Content-Type": "application/json"}).json()

    def get_json(self, path):
        return self.request("GET", path).json()

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
