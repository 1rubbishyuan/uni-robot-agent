"""Lossless encoding for provider image transport."""

from __future__ import annotations

import base64
import io
from functools import lru_cache


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
