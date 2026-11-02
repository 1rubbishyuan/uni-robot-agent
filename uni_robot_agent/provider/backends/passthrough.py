"""Preserve model payloads while selecting an explicit wire protocol."""
from urllib.parse import urlsplit
from uni_robot_agent.provider.contracts import EncodedRequest, DecodedResponse


def responses_url(upstream_base_url: str, request_path: str) -> str:
    parsed = urlsplit(request_path)
    path = parsed.path
    if path == "/v1":
        suffix = ""
    elif path.startswith("/v1/"):
        suffix = path[len("/v1") :]
    else:
        suffix = path
    target = upstream_base_url.rstrip("/") + suffix
    return f"{target}?{parsed.query}" if parsed.query else target



class PassthroughAdapter:
    def __init__(self, protocol: str):
        if protocol not in {"responses", "messages"}:
            raise ValueError(f"Unsupported passthrough protocol: {protocol}")
        self.protocol = protocol

    @property
    def profile_name(self):
        return f"passthrough.{self.protocol}"

    def target_url(self, upstream_base_url, request_path):
        if self.protocol == "responses":
            return responses_url(upstream_base_url, request_path)
        parsed = urlsplit(request_path)
        suffix = parsed.path
        if not suffix.startswith("/v1/"):
            suffix = "/v1" + suffix
        base = upstream_base_url.rstrip("/")
        if base.endswith("/v1"):
            suffix = suffix.removeprefix("/v1")
        return base + suffix + ("?" + parsed.query if parsed.query else "")

    def encode_request(self, request):
        return EncodedRequest(request, {}, request)

    def decode_response(self, response, decode_state):
        return DecodedResponse(response, response)
