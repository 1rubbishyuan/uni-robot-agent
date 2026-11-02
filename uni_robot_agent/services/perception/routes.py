"""Typed perception endpoints mounted on the common ASGI host."""
import asyncio

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from uni_robot_agent.services.errors import ServiceError

from .backend import InvalidImage, InvalidQuery
from .codec import decode_request, encode_result
from .contracts import PerceptionInfo, SegmentRequest, SegmentResponse
from .service import PerceptionService


def build_router(service: PerceptionService) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def health():
        return {"status": "ok"}

    @router.get("/info", response_model=PerceptionInfo)
    async def info():
        return service.info()

    @router.post("/segment", response_model=SegmentResponse)
    async def segment(request: SegmentRequest):
        try:
            item = await run_in_threadpool(decode_request, request)
            future = service.enqueue(item)
            # Shield the shared batch result from an HTTP disconnect cancellation.
            result = await asyncio.shield(asyncio.wrap_future(future))
            return await run_in_threadpool(encode_result, result)
        except ServiceError:
            raise
        except InvalidImage as exc:
            raise ServiceError("invalid_image", str(exc), status=422) from exc
        except InvalidQuery as exc:
            raise ServiceError("invalid_query", str(exc), status=422) from exc
        except Exception as exc:
            raise ServiceError("backend_failure", str(exc), status=500) from exc

    return router
