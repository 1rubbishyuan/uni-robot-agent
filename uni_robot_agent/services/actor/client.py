"""One actor client for every backend, with explicit session recovery."""

import json
import threading
from uuid import uuid4

from uni_robot_agent.services.errors import HttpCallError
from uni_robot_agent.services.http import HttpTransport

from .contracts import (
    ActorInfo,
    ActorObservation,
    ActorProfile,
    CreateSession,
    PredictReply,
    PredictRequest,
    Receipt,
    SessionInfo,
    validate_vector,
)


class ActorUncertain(RuntimeError):
    """The request may have advanced model state; query its receipt before continuing."""


class ActorClient:
    def __init__(
        self,
        url: str,
        *,
        expected_profile: ActorProfile | str | None = None,
        checkpoint_sha256: str | None = None,
        timeout_s=450,
        tracer=None,
    ):
        self.transport = HttpTransport(url, timeout_s=timeout_s, tracer=tracer)
        self._lock = threading.Lock()
        self._closed = False
        self.pending: PredictRequest | None = None
        self.poisoned = False
        try:
            self.info = ActorInfo.model_validate(self.transport.get_json("/info"))
            if isinstance(expected_profile, ActorProfile):
                matches = self.info.profile == expected_profile
            else:
                matches = (
                    expected_profile is None
                    or self.info.profile.name == expected_profile
                )
            if not matches:
                raise ValueError("Actor profile does not match the environment binding")
            if checkpoint_sha256 and self.info.checkpoint_sha256 != checkpoint_sha256:
                raise ValueError(
                    "Actor checkpoint does not match the configured checkpoint"
                )
            creation = CreateSession(
                model_id=self.info.model_id, profile=self.info.profile.name
            )
            self.session = SessionInfo.model_validate(
                self.transport.post_json("/sessions", creation.model_dump())
            )
            if (
                self.session.model_id != self.info.model_id
                or self.session.next_sequence != 0
                or self.session.status != "ready"
            ):
                raise ValueError("Actor returned an invalid new session")
        except Exception:
            self.transport.close()
            raise

    def _validate_reply(self, reply, request):
        reply = PredictReply.model_validate(reply)
        if (
            reply.session_id != request.session_id
            or reply.request_id != request.request_id
            or reply.sequence != request.sequence
            or reply.model_id != self.info.model_id
            or reply.checkpoint_sha256 != self.info.checkpoint_sha256
            or reply.profile != self.info.profile.name
            or len(reply.actions) != self.info.profile.action_horizon
            or any(
                len(row) != len(self.info.profile.action_fields)
                for row in reply.actions
            )
        ):
            raise ValueError("Actor response identity or action profile mismatch")
        for row in reply.actions:
            validate_vector(row, self.info.profile.action_fields, "action")
        return reply

    def predict(self, observation: ActorObservation) -> PredictReply:
        with self._lock:
            if self._closed:
                raise RuntimeError("Actor client is closed")
            if self.poisoned:
                raise RuntimeError("Actor session failed; inference cannot be replayed")
            if self.pending is not None:
                raise ActorUncertain(
                    "Actor request is uncertain; reconcile its receipt before continuing"
                )
            if set(observation.images) != set(self.info.profile.cameras):
                raise ValueError("Actor observation cameras differ from its profile")
            validate_vector(observation.state, self.info.profile.observation_fields, "observation")
            request = PredictRequest(
                session_id=self.session.session_id,
                request_id=uuid4().hex,
                sequence=self.session.next_sequence,
                observation=observation,
            )
            self.pending = request
            try:
                raw = self.transport.post_json(
                    "/predict", request.model_dump(mode="json")
                )
                reply = self._validate_reply(raw, request)
            except HttpCallError as exc:
                try:
                    error = json.loads(exc.content)["error"]
                    accepted = error["details"]["accepted"]
                except (KeyError, TypeError, ValueError):
                    accepted = None
                if accepted is False:
                    self.pending = None
                    raise
                if (
                    accepted is True
                    and error.get("code") == "inference_failed"
                    and error["details"].get("request_id") == request.request_id
                    and error["details"].get("session_id") == request.session_id
                    and error["details"].get("sequence") == request.sequence
                ):
                    self.pending = None
                    self.poisoned = True
                    raise
                raise ActorUncertain(
                    "Actor request status is unknown; query its receipt"
                ) from exc
            except Exception as exc:
                raise ActorUncertain(
                    "Actor response was unavailable or invalid; query its receipt"
                ) from exc
            self.session.next_sequence += 1
            self.pending = None
            return reply

    def reconcile(self) -> Receipt | None:
        with self._lock:
            if self.pending is None:
                return None
            request = self.pending
            try:
                receipt = Receipt.model_validate(
                    self.transport.get_json(
                        f"/sessions/{request.session_id}/requests/{request.request_id}"
                    )
                )
                if (
                    receipt.session_id != request.session_id
                    or receipt.request_id != request.request_id
                    or receipt.sequence != request.sequence
                ):
                    raise ValueError("Actor receipt identity mismatch")
                if receipt.status == "succeeded":
                    self._validate_reply(receipt.reply, request)
                    self.session.next_sequence += 1
                    self.pending = None
                elif receipt.status == "failed":
                    self.poisoned, self.pending = True, None
                return receipt
            except Exception as exc:
                raise ActorUncertain(
                    "Actor receipt is unavailable; request cannot be replayed"
                ) from exc

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.transport.request("DELETE", f"/sessions/{self.session.session_id}")
            except HttpCallError as error:
                try:
                    code = json.loads(error.content)["error"]["code"]
                except (KeyError, TypeError, ValueError):
                    code = None
                # An expired session has already released its model state.
                if error.status not in {404, 410} or code != "session_missing":
                    raise
            finally:
                self.transport.close()
