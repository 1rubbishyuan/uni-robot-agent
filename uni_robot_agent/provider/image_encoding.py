"""Lossless encoding for provider image transport."""

from __future__ import annotations

import base64
import io
from functools import lru_cache


def encode_chat_images(messages: list[dict]) -> None:
    """Encode images in a provider-owned Chat body without touching other parts."""
    for message in messages:
        if isinstance(message.get("content"), list):
            for part in message["content"]:
                if part.get("type") == "image_url":
                    part["image_url"] = dict(part["image_url"],
                        url=lossless_image_url(part["image_url"]["url"]))


def encode_messages_images(messages: list[dict]) -> None:
    """Encode inline PNGs in provider-owned Messages content and tool results."""
    def encode_content(content: list[dict]) -> None:
        for part in content:
            if part.get("type") == "tool_result" and isinstance(part.get("content"), list):
                encode_content(part["content"])
            elif part.get("type") == "image":
                source = part.get("source", {})
                if source.get("type") == "base64" and source.get("media_type") == "image/png":
                    url = lossless_image_url("data:image/png;base64," + source["data"])
                    part["source"] = dict(source, media_type="image/webp", data=url.split(",", 1)[1])

    for message in messages:
        if isinstance(message.get("content"), list):
            encode_content(message["content"])


@lru_cache(maxsize=256)
def lossless_image_url(url: str) -> str:
    """Preserve PNG pixels and dimensions; pass other formats and remote URLs through."""
    if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
        return url
    from PIL import Image

    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True, exact=True, method=4)
    return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode("ascii")
