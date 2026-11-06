"""Model transport and response delivery contracts."""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable


@dataclass(frozen=True)
class RequestContext:
    metadata: dict[str, str] = field(default_factory=dict)
    preserve_history: bool = False


@dataclass(frozen=True)
class PreparedReply:
    body: bytes
    exchange_id: str
    metadata: dict[str, str]
    tool_calls: tuple[dict[str, Any], ...] = ()


class ModelBinding(Protocol):
    """SDK semantics required before and after local response delivery."""

    def request_context(self, request: dict[str, Any] | None) -> RequestContext: ...

    def prepare_response(
        self,
        body: bytes,
        exchange_id: str,
        metadata: dict[str, str],
    ) -> PreparedReply: ...

    def response_written(self, reply: PreparedReply) -> None: ...

    def response_failed(self, reply: PreparedReply) -> None: ...


@dataclass(frozen=True)
class ModelResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    attempts: tuple[dict[str, Any], ...]


class ModelProtocol(Protocol):
    """Wire behavior injected into the shared transport and trace lifecycle."""

    name: str
    trace_client: str

    def matches(self, path: str) -> bool: ...

    def buffered_stream_response(
        self, body: dict | None, request: Any, forward: Any
    ): ...

    def generation_request(
        self, request: dict | None, parallel_tool_calls: bool
    ) -> dict: ...

    def validate_context(self, context_manager: Any) -> None: ...

    def response_failure(self, body: bytes) -> dict | None: ...

    def response_usage(self, body: bytes) -> dict: ...

    def error_response(self, failure: dict) -> dict: ...

    def exception_response(self, error: Exception) -> dict: ...


@runtime_checkable
class LocalRequestProtocol(Protocol):
    """Protocol-owned auxiliary replies outside model-generation accounting."""

    def local_response(self, path: str) -> ModelResponse | None: ...


@runtime_checkable
class RoutedModelProtocol(Protocol):
    """Protocols whose model/method is encoded in the URL rather than the body."""

    def normalize_request(self, path: str, body: dict | None) -> dict: ...

    def response_headers(self, request: dict, headers: Mapping[str, str]) -> Mapping[str, str]: ...


if TYPE_CHECKING:
    from uni_robot_agent.services.http import HttpTransport


@dataclass(frozen=True)
class EncodedRequest:
    """Provider body, per-request decode state, and trainable request view."""

    body: dict[str, Any]
    decode_state: dict[str, Any]
    training_body: dict[str, Any]


@dataclass(frozen=True)
class DecodedResponse:
    """Client reply and trainable response view, without mutating request decode_state."""

    body: bytes
    training_body: bytes


class ProviderAdapter(Protocol):
    profile_name: str

    def target_url(self, upstream_base_url: str, request_path: str) -> str: ...

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest: ...

    def decode_response(
        self,
        response: bytes,
        decode_state: dict[str, Any],
    ) -> DecodedResponse: ...


@runtime_checkable
class RequestValidatingAdapter(Protocol):
    """Optional wire validation before context selection can discard input."""

    def validate_request(self, request: dict[str, Any]) -> None: ...


class ProviderSession(Protocol):
    """Optional resources scoped to one ModelClient, separate from pure encoding."""

    def prepare_request(self, body: dict, headers: dict, exchange: dict) -> None: ...

    def finish_request(self, exchange: dict | None) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class SessionAdapter(Protocol):
    def open_session(
        self, transport: HttpTransport, base_url: str
    ) -> ProviderSession: ...
