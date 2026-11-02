"""The actor wire format uses explicit RGB PNG images."""

import base64
import io

import numpy as np
from PIL import Image as PILImage

from .contracts import Image


def encode_image(pixels: np.ndarray) -> Image:
    pixels = np.asarray(pixels)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("Expected an HWC uint8 RGB image")
    stream = io.BytesIO()
    PILImage.fromarray(pixels).save(stream, format="PNG")
    return Image(data=base64.b64encode(stream.getvalue()).decode("ascii"))


def decode_image(image: Image) -> np.ndarray:
    raw = base64.b64decode(image.data, validate=True)
    with PILImage.open(io.BytesIO(raw)) as decoded:
        if decoded.format != "PNG" or decoded.mode != "RGB":
            raise ValueError("Expected an RGB PNG")
        if decoded.width * decoded.height > 16_777_216:
            raise ValueError("Actor image exceeds 16 megapixels")
        return np.asarray(decoded).copy()
