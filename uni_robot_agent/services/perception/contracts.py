"""Versioned segmentation requests and responses, independent of model runtimes."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class TextQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: Literal["text"] = "text"
    text: str = Field(min_length=1)


class PointQuery(BaseModel):
    """One positive point in pixel coordinates, ordered as (row, column)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["point"] = "point"
    point: tuple[Annotated[StrictInt, Field(ge=0)], Annotated[StrictInt, Field(ge=0)]]


SegmentQuery = Annotated[TextQuery | PointQuery, Field(discriminator="kind")]
SegmentStatus = Literal["matched", "no_match", "below_threshold", "empty_mask"]


class PerceptionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["perception"] = "perception"
    protocol_version: Literal[2] = 2
    model: str
    capabilities: list[str]
    batching: dict[str, int | float | bool]


class SegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    image_base64: str
    query: SegmentQuery
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)


class MaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encoding: Literal["png"] = "png"
    data_base64: str = Field(min_length=1)
    shape: tuple[Annotated[StrictInt, Field(gt=0)], Annotated[StrictInt, Field(gt=0)]]


class SegmentResponse(BaseModel):
    """One semantic outcome, with a PNG mask only when a match is usable."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: SegmentStatus
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    box_xyxy: tuple[float, float, float, float] | None = None
    mask: MaskPayload | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_outcome(self) -> SegmentResponse:
        if (self.status == "matched") != (self.mask is not None):
            raise ValueError("only matched segmentation requires a mask")
        if self.status == "no_match":
            if self.score is not None or self.box_xyxy is not None:
                raise ValueError("no_match cannot contain a score or box")
        elif self.score is None:
            raise ValueError("a selected candidate requires a score")
        if self.box_xyxy is not None:
            x0, y0, x1, y1 = self.box_xyxy
            if x1 < x0 or y1 < y0:
                raise ValueError("box_xyxy coordinates must be ordered")
        return self
