"""Persist provider proxy wire traces."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from uni_robot_agent.provider.recording.images import externalize_images
from uni_robot_agent.provider.protocols.responses import response_usage
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("provider.recording.exchanges")


class ExchangeStore:
    """Write large objects to disk; retain only exchange diagnostics in memory."""

    def __init__(
        self,
        trace_dir: Path | str | None = None,
        *,
        artifacts=None,
        save_raw_request: bool = False,
        save_exchanges: bool = True,
        client_name: str = "codex",
        usage_reader=response_usage,
    ) -> None:
        self._trace_dir = Path(trace_dir) if trace_dir is not None else None
        self._save_raw_request = save_raw_request
        self._save_exchanges = save_exchanges
        self._client_name = client_name
        self._usage_reader = usage_reader
        if self._trace_dir is not None and artifacts is None:
            raise ValueError("Persistent provider traces require the session ArtifactStore")
        self._artifacts = artifacts
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._first_index = 0
        if self._trace_dir is not None:
            self._trace_dir.mkdir(parents=True, exist_ok=True)
            # Interactive runs share one archive; each gateway owns only its new exchanges.
            indices = [int(path.name[9:]) for path in self._trace_dir.glob("exchange_[0-9]*")
                       if path.is_dir() and path.name[9:].isdecimal()]
            self._first_index = max(indices, default=-1) + 1

    @property
    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._records)

    def begin(
        self,
        exchange: dict[str, Any],
        *,
        request_bytes: bytes,
    ) -> dict[str, Any]:
        with self._lock:
            exchange["index"] = self._first_index + len(self._records)
            self._records.append({})
        if self._trace_dir is not None:
            def write(directory: Path) -> None:
                if self._save_exchanges and self._save_raw_request:
                    _write_gzip(directory / "request.json.gz", request_bytes)
                self._write_view(
                    directory / f"request.{self._client_name}.json.gz",
                    exchange.get("client_request"),
                    save=self._save_exchanges,
                )

            self._persist(exchange, write)
        self._update_record(exchange)
        return exchange

    def prepared(self, exchange: dict[str, Any]) -> None:
        if self._trace_dir is not None:
            digest: str | None = None

            def write(directory: Path) -> None:
                nonlocal digest
                self._write_view(
                    directory / "request.context.json.gz",
                    exchange.get("request_after_context"),
                    save=self._save_exchanges,
                )
                self._write_view(
                    directory / "request.provider.json.gz",
                    exchange.get("request_to_provider"),
                    save=self._save_exchanges,
                )
                digest = self._write_view(
                    directory / "request.training.json.gz",
                    exchange.get("request"),
                )

            self._persist(exchange, write)
            exchange["request_digest"] = digest
        elif isinstance(exchange.get("request"), dict):
            # Adjacent retry diagnostics also work when disk tracing is disabled.
            canonical = json.dumps(
                exchange["request"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode()
            exchange["request_digest"] = hashlib.sha256(canonical).hexdigest()
        self._update_record(exchange)

    def finish(self, exchange: dict[str, Any]) -> None:
        exchange["usage"] = self._usage_reader(exchange.get("response") or b"")
        if self._trace_dir is not None:
            def write(directory: Path) -> None:
                if self._save_exchanges:
                    _write_optional_gzip(
                        directory / "response.body.gz",
                        exchange.get("response_from_provider"),
                    )
                    _write_optional_gzip(
                        directory / f"response.{self._client_name}.body.gz",
                        exchange.get("client_response"),
                    )
                _write_optional_gzip(
                    directory / "response.training.body.gz",
                    exchange.get("response"),
                )

            self._persist(exchange, write)

        record = _record(exchange, self._exchange_dir(exchange), self._client_name)
        if self._trace_dir is not None:
            self._persist(
                exchange,
                lambda directory: _write_json(directory / "meta.json", record),
            )
            record = _record(exchange, self._exchange_dir(exchange), self._client_name)
        self._set_record(exchange["index"], record)

    def iter_training_exchanges(
        self, *, thread_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        if self._trace_dir is None:
            raise RuntimeError("training exchanges require a trace directory")
        for record in self.records:
            if thread_id is not None and record.get("thread_id") != thread_id:
                continue
            directory = self._trace_dir / f"exchange_{record['index']:04d}"
            request_path = directory / "request.training.json.gz"
            request = (
                json.loads(_read_optional_gzip(request_path))
                if request_path.exists()
                else _read_optional_json(directory / "request.training.json")
            )
            yield {
                **record,
                "request": request,
                "response": _read_optional_gzip(
                    directory / "response.training.body.gz"
                ),
            }

    def release_training_payloads(self, *, thread_id: str | None = None) -> None:
        """Release this run's temporary inputs only after a successful SFT export."""
        records = self.records
        if self._save_exchanges or self._trace_dir is None or any(
            item.get("persistence_error") or item.get("finished_at") is None
            for item in records
        ):
            return
        for record in records:
            if thread_id is not None and record.get("thread_id") != thread_id:
                continue
            def release(directory: Path) -> None:
                for name in ("request.training.json.gz", "response.training.body.gz"):
                    (directory / name).unlink(missing_ok=True)
                record["response_training_path"] = None
                _write_json(directory / "meta.json", record)

            # Keep metadata and all images; never remove an exchange directory.
            self._persist(record, release)
            self._set_record(record["index"], record)
            if record.get("persistence_error"):
                break

    def persist_report(self, report: dict[str, Any]) -> None:
        if self._trace_dir is None:
            return
        try:
            _write_json(
                self._trace_dir / "report.json",
                {**report, "save_exchanges": self._save_exchanges},
            )
        except Exception as exc:
            logger.error("Failed to persist provider report: %s", exc)

    def _write_view(self, path: Path, value: Any, *, save: bool = True) -> str | None:
        if not isinstance(value, dict):
            return None
        projected = externalize_images(value, self._artifacts)
        if save:
            _write_gzip(path, json.dumps(projected, ensure_ascii=False, indent=2).encode())
        canonical = json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _persist(
        self,
        exchange: dict[str, Any],
        action: Callable[[Path], None],
    ) -> None:
        try:
            directory = self._exchange_dir(exchange)
            if directory is None:
                return
            directory.mkdir(parents=True, exist_ok=True)
            action(directory)
        except Exception as exc:
            exchange["persistence_error"] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Failed to persist provider exchange %s: %s",
                exchange.get("index"),
                exchange["persistence_error"],
            )

    def _exchange_dir(self, exchange: dict[str, Any]) -> Path | None:
        if self._trace_dir is None:
            return None
        return self._trace_dir / f"exchange_{exchange['index']:04d}"

    def _update_record(self, exchange: dict[str, Any]) -> None:
        self._set_record(
            exchange["index"],
            _record(exchange, self._exchange_dir(exchange), self._client_name),
        )

    def _set_record(self, index: int, record: dict[str, Any]) -> None:
        with self._lock:
            self._records[index - self._first_index] = record


def attach_wire_views(
    exchange: dict[str, Any],
    *,
    client_request: dict[str, Any] | None,
    request_after_context: dict[str, Any] | None,
    request_to_provider: dict[str, Any] | None,
    response_from_provider: bytes,
    client_response: bytes,
    profile: str,
) -> None:
    exchange["client_request"] = client_request
    exchange["request_after_context"] = request_after_context
    exchange["request_to_provider"] = request_to_provider
    exchange["response_from_provider"] = response_from_provider
    exchange["client_response"] = client_response
    exchange["wire_profile"] = profile

    # Compatibility fields describe the actual model training view; adapters may replace them.
    exchange["request"] = request_to_provider
    exchange["response"] = response_from_provider


def response_error_detail(value: Any, *, max_chars: int = 2000) -> str:
    if not isinstance(value, bytes) or not value:
        return ""
    text = value.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        body = json.loads(text)
    except ValueError:
        detail = text
    else:
        detail_value: Any = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(detail_value, dict):
            message = detail_value.get("message") or detail_value.get("detail")
            code = detail_value.get("code") or detail_value.get("type")
            if message and code:
                detail = f"{code}: {message}"
            elif message:
                detail = str(message)
            else:
                detail = json.dumps(detail_value, ensure_ascii=False, default=str)
        elif isinstance(detail_value, str):
            detail = detail_value
        else:
            detail = json.dumps(detail_value, ensure_ascii=False, default=str)
    return detail if len(detail) <= max_chars else detail[:max_chars] + "..."


def _record(
    exchange: dict[str, Any],
    directory: Path | None,
    client_name: str = "codex",
) -> dict[str, Any]:
    agent_metadata = exchange.get("agent_metadata") or {}
    response_detail = ""
    if exchange.get("status") != 200 or exchange.get("error"):
        response_detail = response_error_detail(exchange.get("response"))
    return {
        "trace_format_version": 5,
        "index": exchange["index"],
        "request_url": exchange.get("request_url"),
        "request_json_valid": isinstance(exchange.get("client_request"), dict),
        "request_digest": exchange.get("request_digest"),
        "status": exchange.get("status"),
        "http_status": exchange.get("status"),
        "provider_complete": bool(exchange.get("provider_complete")),
        "delivered": bool(exchange.get("delivered")),
        "complete": bool(exchange.get("complete")),
        "client_disconnected": bool(exchange.get("client_disconnected")),
        "delivery_error": exchange.get("delivery_error"),
        "response_headers": exchange.get("response_headers") or {},
        "response_detail": response_detail,
        "error": exchange.get("error"),
        "response_error": exchange.get("response_error"),
        "error_traceback": exchange.get("error_traceback"),
        "provider_retry_count": max(
            0, len(exchange.get("provider_attempts") or []) - 1
        ),
        "provider_attempts": exchange.get("provider_attempts") or [],
        "provider_files": exchange.get("provider_files") or [],
        "started_at": exchange.get("started_at"),
        "finished_at": exchange.get("finished_at"),
        "persistence_error": exchange.get("persistence_error"),
        "usage": exchange.get("usage"),
        "request_path": _existing_name(directory, "request.json.gz"),
        "response_path": _existing_name(directory, "response.body.gz"),
        f"response_{client_name}_path": _existing_name(
            directory, f"response.{client_name}.body.gz"
        ),
        "response_training_path": _existing_name(
            directory, "response.training.body.gz"
        ),
        "wire_profile": exchange.get("wire_profile"),
        "context_policy": exchange.get("context_policy"),
        "thread_id": agent_metadata.get("thread_id"),
        "parent_thread_id": agent_metadata.get("parent_thread_id"),
        "turn_id": agent_metadata.get("turn_id"),
        "thread_source": agent_metadata.get("thread_source"),
    }


def _existing_name(directory: Path | None, name: str) -> str | None:
    return name if directory is not None and (directory / name).exists() else None


def _write_optional_gzip(path: Path, value: Any) -> None:
    if isinstance(value, bytes):
        _write_gzip(path, value)


def _write_gzip(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with gzip.open(temporary, "wb", compresslevel=1) as stream:
        stream.write(data)
    temporary.replace(path)


def _read_optional_gzip(path: Path) -> bytes:
    if not path.exists():
        return b""
    with gzip.open(path, "rb") as stream:
        return stream.read()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def get_model_exchange_error(
    exchanges: Iterable[dict[str, Any]], *, interrupted: bool = False,
) -> str | None:
    """Summarize provider failures; retain full details in the trace directory."""
    exchanges = list(exchanges)
    if interrupted and exchanges:
        tail = exchanges[-1]
        # A budget cut can leave a request pending or disconnect a valid response.
        pending = (tail.get("started_at") is not None and tail.get("finished_at") is None
                   and tail.get("status") is None)
        disconnected = (tail.get("client_disconnected") and tail.get("provider_complete")
                        and tail.get("status") == 200 and "response_error" in tail
                        and not tail["response_error"])
        if (pending or disconnected) and not tail.get("error"):
            exchanges.pop()  # Resolve earlier retries without hiding an unresolved failure.
    for index, (exchange, following) in enumerate(iter_adjacent_exchanges(exchanges)):
        if not exchange.get("complete") or exchange.get("status") != 200:
            request = exchange.get("request")
            if is_immediate_retry(
                exchange, following, request
            ):
                continue
            return exchange_failure_detail(exchange, index)
    return None


def iter_adjacent_exchanges(
    exchanges: Iterable[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    iterator = iter(exchanges)
    try:
        current = next(iterator)
    except StopIteration:
        return
    for following in iterator:
        yield current, following
        current = following
    yield current, None


def is_immediate_retry(
    exchange: dict[str, Any],
    following: dict[str, Any] | None,
    request: dict[str, Any] | None,
) -> bool:
    """Equivalent adjacent requests indicate a failed attempt absent from model context."""
    if following is None:
        return False
    digest = exchange.get("request_digest")
    following_digest = following.get("request_digest")
    if isinstance(digest, str) and digest and isinstance(following_digest, str):
        return digest == following_digest
    return isinstance(request, dict) and following.get("request") == request


def exchange_failure_detail(exchange: dict[str, Any], index: int) -> str:
    parts = [f"Responses exchange {exchange.get('index', index)} failed"]
    status = exchange.get("status")
    if status is not None:
        parts.append(f"HTTP {status}")
    if error := exchange.get("error"):
        parts.append(str(error))
    body_detail = exchange.get("response_detail") or response_error_detail(
        exchange.get("response")
    )
    if body_detail:
        parts.append(str(body_detail))

    headers = exchange.get("response_headers") or {}
    diagnostic_headers = {
        str(key): value
        for key, value in headers.items()
        if str(key).lower() in {"retry-after", "request-id", "x-request-id"}
        or str(key).lower().startswith("x-ratelimit-")
    }
    if diagnostic_headers:
        parts.append(
            "headers="
            + json.dumps(diagnostic_headers, ensure_ascii=False, separators=(",", ":"))
        )
    return "; ".join(parts)
