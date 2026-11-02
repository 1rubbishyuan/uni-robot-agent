"""In-memory segmentation boundary, independent of HTTP and image encoding."""
from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Protocol

import numpy as np

from .contracts import PointQuery, SegmentStatus, TextQuery


class InvalidImage(ValueError):
    """The supplied image cannot be decoded."""


class InvalidQuery(ValueError):
    """The supplied query is not valid for the decoded image."""


@dataclass(frozen=True)
class SegmentInput:
    image_bytes: bytes
    query: TextQuery | PointQuery
    min_score: float


@dataclass(frozen=True)
class SegmentationResult:
    status: SegmentStatus
    mask: np.ndarray | None = None
    score: float | None = None
    box_xyxy: tuple[float, float, float, float] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"matched", "no_match", "below_threshold", "empty_mask"}:
            raise ValueError(f"Unknown segmentation status: {self.status}")
        if self.status == "matched":
            if (not isinstance(self.mask, np.ndarray) or self.mask.ndim != 2
                    or self.mask.dtype != np.bool_ or not self.mask.any()):
                raise ValueError("matched segmentation requires a nonempty 2D boolean mask")
        elif self.mask is not None:
            raise ValueError("only matched segmentation can contain a mask")
        if self.status == "no_match":
            if self.score is not None or self.box_xyxy is not None:
                raise ValueError("no_match cannot contain a score or box")
        elif self.score is None or not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("a selected candidate requires a finite score between 0 and 1")
        if self.box_xyxy is not None:
            if len(self.box_xyxy) != 4 or not all(math.isfinite(v) for v in self.box_xyxy):
                raise ValueError("box_xyxy must contain four finite coordinates")
            x0, y0, x1, y1 = self.box_xyxy
            if x1 < x0 or y1 < y0:
                raise ValueError("box_xyxy coordinates must be ordered")
            object.__setattr__(self, "box_xyxy", tuple(float(v) for v in self.box_xyxy))


class PerceptionBackend(Protocol):
    name: str
    capabilities: Sequence[str]
    batching_capabilities: dict[str, bool]

    def process_batch(
        self, requests: Sequence[SegmentInput],
    ) -> Sequence[SegmentationResult | BaseException]: ...

    def close(self) -> None: ...
