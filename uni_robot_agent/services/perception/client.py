"""Typed perception client using the shared HTTP transport."""
from __future__ import annotations

import numpy as np

from uni_robot_agent.services.http import HttpTransport

from .backend import SegmentationResult
from .codec import decode_result, encode_image
from .contracts import PerceptionInfo, PointQuery, SegmentRequest, SegmentResponse, TextQuery


class PerceptionClient:
    """One segmentation client for every conforming perception backend."""

    def __init__(self, endpoint: str, *, timeout_s: float = 450, tracer=None):
        self._http = HttpTransport(endpoint, timeout_s=timeout_s, tracer=tracer)

    def info(self) -> PerceptionInfo:
        payload = self._http.get_json("/info")
        if payload.get("service") != "perception" or payload.get("protocol_version") != 2:
            raise RuntimeError("Incompatible perception service; protocol version 2 is required")
        return PerceptionInfo.model_validate(payload)

    def close(self) -> None:
        self._http.close()

    def segment(
        self,
        image: bytes | bytearray | memoryview | np.ndarray,
        *,
        query: TextQuery | PointQuery,
        min_score: float = 0.2,
    ) -> SegmentationResult:
        """Segment an image with a text query or a (row, column) point query."""
        image_base64, image_shape = encode_image(image)
        request = SegmentRequest(image_base64=image_base64, query=query, min_score=min_score)
        payload = self._http.post_json("/segment", request.model_dump(mode="json"))
        return decode_result(SegmentResponse.model_validate(payload), image_shape=image_shape)
