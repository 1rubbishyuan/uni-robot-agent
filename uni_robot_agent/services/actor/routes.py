"""Typed actor routes; hosting and tracing are shared with all services."""

import asyncio

from fastapi import APIRouter

from .contracts import (
    ActorInfo,
    CreateSession,
    PredictReply,
    PredictRequest,
    Receipt,
    SessionInfo,
)
from .service import ActorService


def build_router(service: ActorService) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def health():
        return {"status": "ok"}

    @router.get("/info", response_model=ActorInfo)
    async def info():
        return service.info

    @router.post("/sessions", response_model=SessionInfo, status_code=201)
    async def create_session(request: CreateSession):
        return service.create_session(request)

    @router.get("/sessions/{session_id}", response_model=SessionInfo)
    async def get_session(session_id: str):
        return service.get_session(session_id)

    @router.delete("/sessions/{session_id}", response_model=SessionInfo)
    async def close_session(session_id: str):
        return service.close_session(session_id)

    @router.get("/sessions/{session_id}/requests/{request_id}", response_model=Receipt)
    async def receipt(session_id: str, request_id: str):
        return service.receipt(session_id, request_id)

    @router.post("/predict", response_model=PredictReply)
    async def predict(request: PredictRequest):
        # Cancellation of the HTTP waiter must not cancel an accepted model request.
        return await asyncio.shield(asyncio.wrap_future(service.enqueue(request)))

    return router
