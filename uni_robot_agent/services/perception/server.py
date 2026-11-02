"""Configure and host a native perception backend."""
from __future__ import annotations

import argparse
import logging
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from uni_robot_agent.services.hosting import create_app, serve

from .routes import build_router
from .service import PerceptionService


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class HttpConfig(_Config):
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8114, ge=0, le=65535)
    max_request_bytes: int = Field(default=128 * 1024 * 1024, gt=0)


class BatchConfig(_Config):
    max_batch_size: int = Field(default=2, gt=0)
    max_wait_ms: float = Field(default=5, ge=0)
    max_queue_size: int = Field(default=64, gt=0)

    @model_validator(mode="after")
    def queue_holds_batch(self):
        if self.max_queue_size < self.max_batch_size:
            raise ValueError("max_queue_size must be at least max_batch_size")
        return self


class PerceptionConfig(_Config):
    backend: Literal["sam3"] = "sam3"
    checkpoint: str = Field(min_length=1)
    cuda_device: int | None = Field(default=None, ge=0)
    http: HttpConfig = Field(default_factory=HttpConfig)
    batching: BatchConfig = Field(default_factory=BatchConfig)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="uni-robot-agent perception service")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--checkpoint")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--cuda-device", type=int)
    parser.add_argument("--max-batch-size", type=int)
    parser.add_argument("--batch-wait-ms", type=float)
    parser.add_argument("--max-queue-size", type=int)
    return parser


def main() -> None:
    parser = _build_argparser()
    args = parser.parse_args()
    settings = tomllib.loads(args.config.read_text()) if args.config else {}
    for key in ("checkpoint", "cuda_device"):
        if getattr(args, key) is not None:
            settings[key] = getattr(args, key)
    for key in ("host", "port"):
        if getattr(args, key) is not None:
            settings.setdefault("http", {})[key] = getattr(args, key)
    for flag, key in (("max_batch_size", "max_batch_size"),
                      ("batch_wait_ms", "max_wait_ms"),
                      ("max_queue_size", "max_queue_size")):
        if getattr(args, flag) is not None:
            settings.setdefault("batching", {})[key] = getattr(args, flag)
    try:
        config = PerceptionConfig.model_validate(settings)
    except ValidationError as exc:
        parser.error(str(exc))
    if config.cuda_device is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(config.cuda_device)
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    from .backends.sam3 import Sam3Backend

    backend = Sam3Backend.load(config.checkpoint)
    service = None
    try:
        service = PerceptionService(backend, **config.batching.model_dump())
        app = create_app(build_router(service), on_close=service.close,
                         max_request_bytes=config.http.max_request_bytes)
        serve(app, host=config.http.host, port=config.http.port)
    finally:
        # Host construction can fail before ASGI lifespan starts, including a busy port.
        if service is not None:
            service.close()
        else:
            backend.close()


if __name__ == "__main__":
    main()
