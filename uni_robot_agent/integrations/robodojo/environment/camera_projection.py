"""Isaac Camera 1.3.6 / Replicator 1.12.27 render calibration boundary.

Read CameraParams only during the existing calibrated image capture, on the native
owner thread. Ordinary tiled RGB observation never attaches parameter readers.
Camera.initialize's aperture K is not the rendered projection.
"""

from __future__ import annotations

import numpy as np

ANNOTATOR = "CameraParams"
PIXEL_COORDINATES = "integer_pixel_centers"


class ProjectionUnavailable(ValueError):
    """The render data cannot calibrate the requested image grid."""


def projection_intrinsic(params, image_size):
    """Convert a USD row-vector perspective matrix to integer-pixel-center K."""
    if params.get("cameraModel") != "pinhole":
        raise ProjectionUnavailable("Render calibration requires a pinhole camera")
    matrix = np.asarray(params.get("cameraProjection", []), dtype=np.float64)
    if matrix.size != 16 or not np.isfinite(matrix).all():
        raise ProjectionUnavailable("Render projection is missing or nonfinite")
    matrix = matrix.reshape(4, 4)
    # The supported form permits off-center principal points, but no skew,
    # orthographic projection or transposed/column-vector matrices.
    zeros = matrix[[0, 0, 0, 1, 1, 1, 3, 3, 3], [1, 2, 3, 0, 2, 3, 0, 1, 3]]
    if (not np.allclose(zeros, 0, atol=1e-9)
            or not np.isclose(matrix[2, 3], -1, atol=1e-9)
            or min(matrix[0, 0], matrix[1, 1]) <= 0):
        raise ProjectionUnavailable("Unsupported render projection convention")
    height, width = image_size
    if height <= 0 or width <= 0:
        raise ProjectionUnavailable("Image dimensions must be positive")
    return np.array([
        [width * matrix[0, 0] / 2, 0, width * (1 - matrix[2, 0]) / 2 - .5],
        [0, height * matrix[1, 1] / 2, height * (1 + matrix[2, 1]) / 2 - .5],
        [0, 0, 1],
    ])


def projection_metadata(params, product, *, tiled=False):
    return {"status": "valid", "source": "render_projection",
            "pixel_coordinates": PIXEL_COORDINATES, "render_product": product,
            "render_resolution": list(map(int, params["renderProductResolution"])),
            "tiled": tiled}


def geometry_projection(camera, annotator, image_size):
    """Read the annotator attached for this capture, never the SDK aperture K."""
    params = annotator.get_data()
    resolution = tuple(params.get("renderProductResolution", ()))
    if resolution != tuple(reversed(image_size)):
        raise ProjectionUnavailable("Geometry projection and RGB dimensions differ")
    intrinsic = projection_intrinsic(params, image_size)
    return intrinsic, projection_metadata(params, camera.get_render_product_path())


def attach_geometry_projection(camera):
    # Read metadata after rendering, outside Camera's acquisition callback
    # and its synchronous render-graph evaluation.
    return _new_annotator(camera.get_render_product_path())


def _new_annotator(product):
    import omni.replicator.core as rep

    annotator = rep.annotators.get(ANNOTATOR)
    try:
        annotator.attach(product)
    except BaseException:
        annotator.detach()
        raise
    return annotator
