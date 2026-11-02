"""PNG/base64 conversion at the perception transport boundary."""
import base64
import binascii
import io

import numpy as np
from PIL import Image

from .backend import InvalidImage, SegmentInput, SegmentationResult
from .contracts import MaskPayload, SegmentRequest, SegmentResponse


def decode_image(image_bytes: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImage("Could not decode input image") from exc


def encode_image(image: bytes | bytearray | memoryview | np.ndarray) -> tuple[str, tuple[int, int]]:
    if isinstance(image, np.ndarray):
        buffer = io.BytesIO()
        Image.fromarray(image).save(buffer, format="PNG")
        image = buffer.getvalue()
    try:
        with Image.open(io.BytesIO(image)) as source:
            width, height = source.size
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImage("Could not read input image dimensions") from exc
    return base64.b64encode(image).decode("ascii"), (height, width)


def decode_request(request: SegmentRequest) -> SegmentInput:
    try:
        image_bytes = base64.b64decode(request.image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidImage("image_base64 is invalid") from exc
    if not image_bytes:
        raise InvalidImage("image_base64 is empty")
    return SegmentInput(image_bytes, request.query, request.min_score)


def encode_result(result: SegmentationResult) -> SegmentResponse:
    payload = None
    if result.mask is not None:
        buffer = io.BytesIO()
        Image.fromarray(result.mask.astype(np.uint8) * 255).save(buffer, format="PNG")
        payload = MaskPayload(
            data_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            shape=result.mask.shape,
        )
    return SegmentResponse(
        status=result.status, score=result.score, box_xyxy=result.box_xyxy,
        mask=payload, reason=result.reason,
    )


def decode_result(response: SegmentResponse, *, image_shape: tuple[int, int]) -> SegmentationResult:
    mask = None
    if response.mask is not None:
        try:
            raw = base64.b64decode(response.mask.data_base64, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or image.mode not in {"1", "L"}:
                    raise ValueError("mask must be a binary grayscale PNG")
                pixels = np.asarray(image.convert("L"))
            if pixels.shape != response.mask.shape:
                raise ValueError("segmentation mask shape does not match the response")
            if pixels.shape != image_shape:
                raise ValueError("segmentation mask shape does not match the input image")
            if not np.all((pixels == 0) | (pixels == 255)):
                raise ValueError("segmentation mask contains nonbinary pixels")
            mask = pixels > 0
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise RuntimeError(f"Could not decode segmentation PNG mask: {exc}") from exc
    try:
        return SegmentationResult(
            status=response.status, mask=mask, score=response.score,
            box_xyxy=response.box_xyxy, reason=response.reason,
        )
    except ValueError as exc:
        raise RuntimeError(f"Invalid segmentation result: {exc}") from exc
