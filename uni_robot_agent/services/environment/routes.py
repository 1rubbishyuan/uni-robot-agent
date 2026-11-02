"""Typed episode routes, mountable in native hosts and authenticated pools."""
from collections.abc import Callable
from typing import Annotated, Generic, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from .contracts import (
    GoalRegionRequest,
    CaptureRequest,
    FinishRequest,
    PreviewRequest,
    ProtocolError,
    RequestIdentity,
    RequestRejected,
    StepRequest,
)
from .service import EnvironmentService, ServiceError

Payload = TypeVar("Payload")


class Envelope(BaseModel, Generic[Payload]):
    model_config = ConfigDict(extra="forbid")
    identity: RequestIdentity
    payload: Payload


class _EpisodeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await handler(request)
            except HTTPException:
                raise
            except RequestValidationError as error:
                failure = ProtocolError(code="invalid_request", message="Request does not match the protocol",
                                        details={"errors": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                                                            for item in error.errors()]})
                status = 422
            except ServiceError as error:
                failure = ProtocolError(code=error.code, message=str(error),
                                        execution_status=error.execution_status, details=dict(error.details))
                status = error.status
            except RequestRejected as error:
                failure = ProtocolError(code="request_rejected", message=str(error))
                status = 422
            except LookupError as error:
                failure = ProtocolError(code="not_found", message=f"Resource is unavailable: {error}")
                status = 404
            except Exception:  # noqa: BLE001 - never misreport unknown physical completion.
                failure = ProtocolError(code="internal_error", message="Episode service failed",
                                        execution_status="unknown" if request.method == "POST" else "not_started")
                status = 500
            service = getattr(request.state, "environment_service", None)
            if service is not None:
                failure.details["next_sequence"] = service.info().next_sequence
            try:
                body = await request.json()
                failure.request_id = RequestIdentity.model_validate(body["identity"]).request_id
            except (ValueError, KeyError, TypeError):
                pass
            return JSONResponse({"error": failure.model_dump(mode="json")}, status_code=status)

        return guarded


def build_router(service: EnvironmentService | None = None, *,
                 resolve_service: Callable[[Request], EnvironmentService] | None = None) -> APIRouter:
    """A pool can resolve and authorize a service from its real request peer."""
    if (service is None) == (resolve_service is None):
        raise ValueError("Provide exactly one service or resolver")
    router = APIRouter(route_class=_EpisodeRoute)

    def selected(request: Request) -> EnvironmentService:
        selected_service = service if service is not None else resolve_service(request)
        request.state.environment_service = selected_service
        episode_id = request.headers.get("X-Episode-ID")
        if episode_id is not None and episode_id != selected_service.info().episode_id:
            raise ServiceError("episode_mismatch", "The episode handle is no longer valid")
        return selected_service

    Current = Annotated[EnvironmentService, Depends(selected)]

    @router.get("/info")
    def info(current: Current):
        return current.info().model_dump(mode="json")

    @router.get("/snapshots/latest")
    def latest_snapshot(current: Current):
        return current.latest_snapshot()

    @router.get("/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str, current: Current):
        return current.snapshot(snapshot_id)

    @router.get("/records")
    def record(current: Current, index: int = -1):
        return current.record(index)

    @router.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: str, current: Current):
        reference = current.artifacts.describe(artifact_id)
        return Response(current.artifacts.read_bytes(artifact_id), media_type=reference["media_type"])

    @router.get("/requests/{request_id}")
    def request_status(request_id: str, current: Current):
        return current.request_status(request_id)

    @router.get("/outcome")
    def outcome(current: Current):
        return current.outcome()

    @router.post("/steps")
    def step(body: Envelope[StepRequest], current: Current):
        """Execute one primitive command; high-level tools own orchestration."""
        return current.step(body.identity, body.payload)

    @router.post("/previews")
    def preview(body: Envelope[PreviewRequest], current: Current):
        return current.preview(body.identity, body.payload)

    @router.post("/goal-regions")
    def goal_region(body: Envelope[GoalRegionRequest], current: Current):
        return current.solve_goal_region(body.identity, body.payload)

    from .device_contracts import DeviceRequest, InterruptRequest

    @router.get("/device-state")
    def device_state(current: Current):
        return current.device_state()

    @router.post("/device-steps")
    def device_step(body: Envelope[DeviceRequest], current: Current):
        return current.device_step(body.identity, body.payload)

    @router.post("/device-previews")
    def device_preview(body: Envelope[DeviceRequest], current: Current):
        return current.device_preview(body.identity, body.payload)

    @router.post("/interrupts")
    def interrupt(body: InterruptRequest, current: Current):
        return current.interrupt(body)

    @router.get("/interrupts/{request_id}")
    def interrupt_status(request_id: str, current: Current):
        current._require_devices()
        with current.interrupts.lock:
            return dict(current.interrupts.receipts[request_id])

    @router.post("/captures")
    def capture(body: Envelope[CaptureRequest], current: Current):
        return current.capture(body.identity, body.payload)

    @router.post("/finish")
    def finish(body: Envelope[FinishRequest], current: Current):
        return current.finish(body.identity, body.payload)

    return router
