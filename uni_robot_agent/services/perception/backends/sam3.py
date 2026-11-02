"""SAM3 model loading, text batching and cached interactive segmentation."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from PIL import Image

from ..backend import InvalidQuery, SegmentationResult, SegmentInput
from ..codec import decode_image
from ..contracts import PointQuery, TextQuery

logger = logging.getLogger(__name__)
_SUPPORTED_SAM3_VERSION = "0.1.4"


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    slot: int
    source: SegmentInput
    image: Image.Image

    @property
    def image_shape(self) -> tuple[int, int]:
        return self.image.height, self.image.width


class Sam3Backend:
    """Batch text-prompt inference and serialize point-prompt inference."""

    def __init__(
        self,
        model: Any,
        processor: Any,
        *,
        device: str = "cuda",
        torch_module: Any | None = None,
    ) -> None:
        self._model = model
        self._processor = processor
        self._device = device
        self._torch = torch_module
        # Only BatchWorker's owner thread accesses this single image-feature slot.
        self._image_digest: str | None = None
        self._image_state: dict[str, Any] | None = None

    @classmethod
    def load(
        cls,
        checkpoint: str,
    ) -> Sam3Backend:
        """Load the official SAM 3.0 model and interactive point head."""
        resolved = Path(checkpoint).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"SAM3 checkpoint not found: {resolved}")
        try:
            import torch
            from sam3.model.sam3_image_processor import Sam3Processor
            from sam3.model_builder import build_sam3_image_model
        except ImportError as exc:
            raise RuntimeError(
                "SAM3 dependencies are missing; prepare a separate service environment "
                "with PyTorch and sam3==0.1.4 (see README: Service environments)."
            ) from exc
        try:
            installed_version = version("sam3")
        except PackageNotFoundError as exc:
            raise RuntimeError("SAM3 package metadata is unavailable") from exc
        if installed_version != _SUPPORTED_SAM3_VERSION:
            raise RuntimeError(
                "SAM3 batching requires sam3=="
                f"{_SUPPORTED_SAM3_VERSION}, found {installed_version}"
            )

        if not torch.cuda.is_available():
            raise RuntimeError("SAM3 requires a CUDA-capable GPU")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.cuda.set_device(0)

        logger.info("loading SAM 3.0 checkpoint: %s", resolved)
        try:
            model = build_sam3_image_model(
                device="cuda",
                checkpoint_path=str(resolved),
                load_from_HF=False,
                enable_inst_interactivity=True,
            )
        except Exception as exc:
            raise RuntimeError(
                f"failed to load SAM 3.0 checkpoint: {resolved}"
            ) from exc
        processor = Sam3Processor(model, device="cuda", confidence_threshold=0.0)
        return cls(
            model,
            processor,
            device="cuda",
            torch_module=torch,
        )

    name = "sam3"
    capabilities = ("text_segmentation", "point_segmentation")
    batching_capabilities: ClassVar[dict[str, bool]] = {"text_prompts": True, "point_prompts": False}

    def close(self) -> None:
        self._image_state = None
        self._image_digest = None

    @contextmanager
    def _inference_context(self):
        if self._torch is None:
            yield
            return
        precision = (
            self._torch.autocast("cuda", dtype=self._torch.bfloat16)
            if self._device.startswith("cuda") else nullcontext()
        )
        with self._torch.inference_mode(), precision:
            yield

    def process_batch(
        self,
        jobs: Sequence[SegmentInput],
    ) -> list[SegmentationResult | BaseException]:
        results: list[SegmentationResult | BaseException | None] = [None] * len(jobs)
        text_requests: list[_PreparedRequest] = []
        point_requests: list[_PreparedRequest] = []
        for slot, job in enumerate(jobs):
            try:
                request = _PreparedRequest(slot, job, decode_image(job.image_bytes))
                if isinstance(job.query, TextQuery):
                    text_requests.append(request)
                else:
                    point_requests.append(request)
            except BaseException as exc:  # noqa: BLE001 - isolate bad images.
                results[slot] = exc

        if text_requests:
            try:
                text_results = (
                    [self._infer_text(text_requests[0])]
                    if len(text_requests) == 1
                    else self._infer_text_batch(text_requests)
                )
            except BaseException as exc:  # noqa: BLE001 - fail only the text lane.
                text_results = [exc] * len(text_requests)
            for request, result in zip(text_requests, text_results, strict=True):
                results[request.slot] = result

        # SAM3 0.1.4 predict_inst hard-codes a batch dimension of one.
        for request in point_requests:
            try:
                results[request.slot] = self._infer_point(request)
            except BaseException as exc:  # noqa: BLE001 - isolate bad points.
                results[request.slot] = exc

        return [
            result
            if result is not None
            else RuntimeError("SAM3 batch request produced no result")
            for result in results
        ]

    def _infer_text_batch(
        self,
        requests: Sequence[_PreparedRequest],
    ) -> list[SegmentationResult | BaseException]:
        if self._torch is None:
            raise RuntimeError("SAM3 text batching requires torch")
        torch = self._torch
        tensors = []
        prompts = []
        for request in requests:
            array = np.array(request.image, dtype=np.uint8, copy=True)
            tensor = torch.from_numpy(array).permute(2, 0, 1).to(self._device)
            tensors.append(self._processor.transform(tensor))
            assert isinstance(request.source.query, TextQuery)
            prompts.append(request.source.query.text)

        with self._inference_context():
            backbone_out = self._model.backbone.forward_image(
                torch.stack(tensors, dim=0)
            )
            self._prepare_interactive_features(backbone_out)
            backbone_out.update(
                self._model.backbone.forward_text(
                    prompts,
                    device=self._device,
                )
            )
            batch_ids = torch.arange(
                len(requests), device=self._device, dtype=torch.long
            )
            find_input = replace(
                self._processor.find_stage,
                img_ids=batch_ids,
                text_ids=batch_ids,
            )
            outputs = self._model.forward_grounding(
                backbone_out=backbone_out,
                find_input=find_input,
                geometric_prompt=self._model._get_dummy_prompt(
                    num_prompts=len(requests)
                ),
                find_target=None,
            )
        return self._collect_text_results(outputs, requests)

    def _infer_text(
        self,
        request: _PreparedRequest,
    ) -> SegmentationResult:
        job = request.source
        assert isinstance(job.query, TextQuery)
        state = self._new_prompt_state(request)
        with self._inference_context():
            output = self._processor.set_text_prompt(
                prompt=job.query.text,
                state=state,
            )
        return self._select_result(
            masks=output.get("masks"),
            scores=output.get("scores"),
            boxes=output.get("boxes"),
            min_score=job.min_score,
            image_shape=request.image_shape,
        )

    def _infer_point(
        self,
        request: _PreparedRequest,
    ) -> SegmentationResult:
        job = request.source
        assert isinstance(job.query, PointQuery)
        row, col = job.query.point
        height, width = request.image_shape
        if row < 0 or col < 0 or row >= height or col >= width:
            raise InvalidQuery(
                f"point [row, col] {job.query.point} is outside image shape "
                f"[{height}, {width}]"
            )
        state = self._new_prompt_state(request)
        predictor = getattr(self._model, "inst_interactive_predictor", None)
        if predictor is None:
            raise RuntimeError("SAM3 instance interactivity is not enabled")
        point_coords = np.asarray([[col, row]], dtype=np.float32)
        point_labels = np.asarray([1], dtype=np.int64)
        try:
            with self._inference_context():
                masks, scores, _ = self._model.predict_inst(
                    state, point_coords=point_coords, point_labels=point_labels,
                    multimask_output=True,
                )
        finally:
            # 0.1.4 only releases these transient fields on successful inference.
            predictor._features = None
            predictor._is_image_set = False
        return self._select_result(
            masks=masks, scores=scores, boxes=None, min_score=job.min_score,
            image_shape=request.image_shape,
        )

    def _new_prompt_state(
        self,
        request: _PreparedRequest,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(request.source.image_bytes).hexdigest()
        if digest != self._image_digest or self._image_state is None:
            with self._inference_context():
                state = self._processor.set_image(request.image)
            self._image_digest = digest
            self._image_state = state
        # 0.1.4 writes language features into backbone_out and results into state.
        # Copy those dicts per prompt; immutable image tensors remain shared.
        return {**self._image_state, "backbone_out": self._image_state["backbone_out"].copy()}

    def _prepare_interactive_features(self, backbone_out: dict[str, Any]) -> None:
        if getattr(self._model, "inst_interactive_predictor", None) is None:
            return
        sam2 = backbone_out.get("sam2_backbone_out")
        if sam2 is None:
            return
        decoder = self._model.inst_interactive_predictor.model.sam_mask_decoder
        sam2["backbone_fpn"][0] = decoder.conv_s0(sam2["backbone_fpn"][0])
        sam2["backbone_fpn"][1] = decoder.conv_s1(sam2["backbone_fpn"][1])

    def _collect_text_results(
        self,
        outputs: dict[str, Any],
        requests: Sequence[_PreparedRequest],
    ) -> list[SegmentationResult | BaseException]:
        boxes = outputs["pred_boxes"]
        masks = outputs["pred_masks"]
        probabilities = outputs["pred_logits"].sigmoid()
        presence = outputs["presence_logit_dec"].sigmoid().unsqueeze(1)
        scores = (probabilities * presence).squeeze(-1)
        if any(value.shape[0] != len(requests) for value in (scores, masks, boxes)):
            raise RuntimeError(
                "SAM3 returned an unexpected text batch shape: "
                f"scores={tuple(scores.shape)}, batch={len(requests)}"
            )
        results: list[SegmentationResult | BaseException] = []
        for index, request in enumerate(requests):
            try:
                results.append(self._select_result(
                    masks=masks[index], scores=scores[index], boxes=boxes[index],
                    min_score=request.source.min_score, image_shape=request.image_shape,
                    raw_text_output=True,
                ))
            except Exception as exc:
                # A malformed candidate belongs to its request, not its neighbors.
                results.append(exc)
        return results

    @staticmethod
    def _scale_box(
        box: Any,
        *,
        height: int,
        width: int,
    ) -> tuple[float, float, float, float]:
        cx, cy, box_width, box_height = box
        return (
            (cx - box_width / 2) * width,
            (cy - box_height / 2) * height,
            (cx + box_width / 2) * width,
            (cy + box_height / 2) * height,
        )

    @staticmethod
    def _as_numpy(value: Any) -> np.ndarray:
        return value if isinstance(value, np.ndarray) else value.detach().float().cpu().numpy()

    def _select_result(
        self, *, masks: Any, scores: Any, boxes: Any, min_score: float,
        image_shape: tuple[int, int], raw_text_output: bool = False,
    ) -> SegmentationResult:
        if masks is None or scores is None:
            raise RuntimeError("SAM3 omitted candidate masks or scores")
        scores_arr = self._as_numpy(scores).reshape(-1)
        if masks.ndim == 4 and masks.shape[1] == 1:
            masks = masks[:, 0]
        if masks.ndim == 2:
            masks = masks[None]
        if masks.ndim != 3 or masks.shape[0] != scores_arr.size:
            raise RuntimeError(
                f"unexpected SAM3 candidate shapes: "
                f"masks={tuple(masks.shape)}, scores={scores_arr.shape}"
            )
        if boxes is not None and tuple(boxes.shape) != (scores_arr.size, 4):
            raise RuntimeError(f"unexpected SAM3 box shape: {tuple(boxes.shape)}")
        if not np.isfinite(scores_arr).all() or np.any((scores_arr < 0) | (scores_arr > 1)):
            raise RuntimeError("SAM3 candidate scores must be finite probabilities")
        # The pinned processor removes zero-score text candidates before selection.
        if scores_arr.size == 0 or (raw_text_output and not np.any(scores_arr > 0)):
            return SegmentationResult(status="no_match", reason="SAM3 returned no candidate")

        # All native paths use the same stable top -> threshold -> empty-mask policy.
        index = int(np.argmax(scores_arr))
        score = float(scores_arr[index])
        box = None
        if boxes is not None:
            box = tuple(float(value) for value in self._as_numpy(boxes[index]))
            if raw_text_output:
                box = self._scale_box(box, height=image_shape[0], width=image_shape[1])
        if score < min_score:
            return SegmentationResult(
                status="below_threshold", score=score, box_xyxy=box,
                reason=f"top score {score:.3f} is below min_score {min_score:.3f}",
            )
        # Copy only the selected mask to CPU, including the native batched text path.
        selected = masks[index]
        if raw_text_output:
            assert self._torch is not None
            selected = self._torch.nn.functional.interpolate(
                selected[None, None].float(), image_shape,
                mode="bilinear", align_corners=False,
            )[0, 0]
            if not self._torch.isfinite(selected).all().item():
                raise RuntimeError("SAM3 mask logits must be finite")
            selected = selected.sigmoid()
        mask_values = self._as_numpy(selected)
        if mask_values.shape != image_shape or not np.isfinite(mask_values).all():
            raise RuntimeError("SAM3 mask must be finite and match the source image shape")
        mask = mask_values > (0.5 if raw_text_output else 0)
        if not mask.any():
            return SegmentationResult(
                status="empty_mask", score=score, box_xyxy=box,
                reason="SAM3 returned an empty mask",
            )
        return SegmentationResult(
            status="matched", score=score, box_xyxy=box, mask=mask,
        )
