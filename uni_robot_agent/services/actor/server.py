"""Start one configured actor instance on the common HTTP host."""

import argparse
import json
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from uni_robot_agent.services.hosting import create_app, serve

from .config import apply_service_limits, resolve_actor
from .registry import backend_names
from .routes import build_router
from .service import ActorService


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    host: str = "127.0.0.1"
    port: int = Field(default=8601, ge=0, le=65535)
    max_request_bytes: int = Field(default=128 * 1024 * 1024, gt=0)


class BatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_batch_size: int = Field(default=2, gt=0)
    max_wait_ms: float = Field(default=5, ge=0)
    max_queue_size: int = Field(default=64, gt=0)


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_sessions: int = Field(default=1024, gt=0)
    idle_timeout_s: float = Field(default=1800, gt=0)
    receipt_ttl_s: float = Field(default=3600, gt=0)
    max_receipts: int = Field(default=128, gt=0)
    max_session_requests: int = Field(default=4096, gt=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--backend", choices=backend_names())
    parser.add_argument("--runtime")
    parser.add_argument("--profile", help="Named actor deployment recipe")
    parser.add_argument("--model-config", dest="model_config_name")
    parser.add_argument("--checkpoint")
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--xpolicylab-root", help="Compatibility alias for --native-root")
    parser.add_argument("--native-root")
    parser.add_argument("--identity-output", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--cuda-device")
    parser.add_argument("--max-batch-size", type=int)
    parser.add_argument("--batch-wait-ms", type=float)
    parser.add_argument("--max-queue-size", type=int)
    args = parser.parse_args()
    settings = tomllib.loads(args.config.read_text()) if args.config else {}
    http_settings = settings.pop("http", {})
    batch_settings = settings.pop("batching", {})
    session_settings = settings.pop("sessions", {})
    for field in (
        "runtime",
        "profile",
        "model_config_name",
        "checkpoint",
        "checkpoint_sha256",
        "xpolicylab_root",
        "native_root",
    ):
        if getattr(args, field) is not None:
            settings[field] = getattr(args, field)
    for field in ("host", "port"):
        if getattr(args, field) is not None:
            http_settings[field] = getattr(args, field)
    for field, config_key in (
        ("max_batch_size", "max_batch_size"),
        ("batch_wait_ms", "max_wait_ms"),
        ("max_queue_size", "max_queue_size"),
    ):
        if getattr(args, field) is not None:
            batch_settings[config_key] = getattr(args, field)
    try:
        resolved = resolve_actor(settings, backend=args.backend)
        apply_service_limits(resolved, batch_settings, session_settings)
    except ValueError as error:
        parser.error(str(error))
    http = HttpConfig.model_validate(http_settings)
    batch = BatchConfig.model_validate(batch_settings)
    sessions = SessionConfig.model_validate(session_settings)
    if batch.max_queue_size < batch.max_batch_size:
        parser.error("max_queue_size must be at least max_batch_size")
    if args.cuda_device is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_device
    model = resolved.definition.create(resolved.config)
    service = None
    try:
        service = ActorService(
            model,
            max_batch_size=batch.max_batch_size,
            batch_wait_ms=batch.max_wait_ms,
            max_queue_size=batch.max_queue_size,
            **sessions.model_dump(),
        )
        if args.identity_output:
            args.identity_output.parent.mkdir(parents=True, exist_ok=True)
            args.identity_output.write_text(
                json.dumps(service.info.model_dump(), indent=2) + "\n"
            )
        app = create_app(
            build_router(service),
            on_close=service.close,
            max_request_bytes=http.max_request_bytes,
        )
        serve(app, host=http.host, port=http.port)
    finally:
        if service is None:
            model.close()
        else:
            service.close()


if __name__ == "__main__":
    main()
