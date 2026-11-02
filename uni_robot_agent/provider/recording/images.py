"""Project embedded provider images into the session's artifact references."""

from __future__ import annotations

import base64
import binascii
from typing import Any

IMAGE_REF_KEY = "$uni_robot_agent_image"


def externalize_images(value: Any, artifacts) -> Any:
    """Preserve wire structure and exact image bytes without owning any files."""
    if isinstance(value, dict):
        mime = value.get("mime_type") or value.get("mimeType") or value.get("media_type")
        if isinstance(mime, str) and mime.startswith("image/") and isinstance(value.get("data"), str):
            return {
                **{key: externalize_images(item, artifacts) for key, item in value.items() if key != "data"},
                "image_url": _image_reference(mime, value["data"], artifacts),
            }
        return {key: externalize_images(item, artifacts) for key, item in value.items()}
    if isinstance(value, list):
        return [externalize_images(item, artifacts) for item in value]
    if isinstance(value, str) and value.startswith("data:image/") and "," in value:
        header, encoded = value[5:].split(",", 1)
        if header.endswith(";base64"):
            return _image_reference(header.removesuffix(";base64"), encoded, artifacts)
    return value


def _image_reference(media_type: str, encoded: str, artifacts) -> dict:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("provider image contains invalid base64") from error
    ref = artifacts.put_bytes(data, media_type)
    # A persisted request must never claim an image whose write already failed.
    artifacts.local_path(ref["artifact_id"])
    return {
        IMAGE_REF_KEY: artifacts.relative_path(ref["artifact_id"]),
        "media_type": media_type,
        "size": len(data),
    }
