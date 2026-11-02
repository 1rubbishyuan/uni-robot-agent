"""Sliding window that trims complete interaction blocks."""

from __future__ import annotations

import copy
import json
from typing import Any

from uni_robot_agent.context.history import (
    ContextBlock,
    ContextBudget,
    ContextDecision,
    ContextRequest,
    estimate_tokens,
    register_context_policy,
)

_CALL_TYPES = {"function_call", "custom_tool_call"}
_OUTPUT_TYPES = {"function_call_output", "custom_tool_call_output"}
_IMAGE_TYPES = {"input_image", "output_image", "image"}
_OBSERVATION_NAMES = {
    "capture_observation",
    "get_current_obs",
    "observe",
    "view_env_state",
    "view_image",
}


class ContextBudgetExceeded(ValueError):
    pass


@register_context_policy("sliding_window")
class SlidingWindowPolicy:
    name = "sliding_window"
    version = "3"

    def __init__(
        self,
        *,
        max_estimated_input_tokens: int,
        target_estimated_input_tokens: int,
        reserve_output_tokens: int = 0,
        keep_recent_blocks: int = 4,
        keep_observation_blocks: int = 1,
        min_recent_tool_images: int = 3,
        image_eviction_batch_size: int = 20,
    ) -> None:
        self._budget = ContextBudget(
            max_estimated_input_tokens=max_estimated_input_tokens,
            target_estimated_input_tokens=target_estimated_input_tokens,
            reserve_output_tokens=reserve_output_tokens,
            keep_recent_blocks=keep_recent_blocks,
            keep_observation_blocks=keep_observation_blocks,
        )
        if min_recent_tool_images < 0:
            raise ValueError("min_recent_tool_images cannot be negative")
        if image_eviction_batch_size < 1:
            raise ValueError("image_eviction_batch_size must be positive")
        self._min_recent_tool_images = min_recent_tool_images
        self._image_eviction_batch_size = image_eviction_batch_size

    @property
    def options(self) -> dict[str, int]:
        return {
            "max_input_tokens": self._budget.max_estimated_input_tokens,
            "target_input_tokens": self._budget.target_estimated_input_tokens,
            "reserve_output_tokens": self._budget.reserve_output_tokens,
            "recent_turns": self._budget.keep_recent_blocks,
            "latest_observations": self._budget.keep_observation_blocks,
            "image_keep_n": self._min_recent_tool_images,
            "image_eviction_batch": self._image_eviction_batch_size,
        }

    def select(self, request: ContextRequest) -> ContextDecision:
        if request.body.get("previous_response_id"):
            raise ValueError(
                "sliding_window does not support previous_response_id; "
                "the provider may still retain removed history"
            )

        before = estimate_tokens(request.body)
        item_ids = tuple(f"item_{index:04d}" for index, _ in enumerate(request.items))

        # Evict old images in batches independently of the token threshold.
        # Batched eviction preserves a more stable shared prompt-cache prefix.
        image_request, evicted_images = _evict_old_images(
            request,
            keep_n=self._min_recent_tool_images,
            batch_size=self._image_eviction_batch_size,
        )
        after_images = estimate_tokens(image_request.body)
        # Preserve requested text and calls while retaining the image window.
        if request.preserve_history or after_images <= self._budget.trigger_tokens:
            return ContextDecision(
                request=image_request,
                policy=self.name,
                version=self.version,
                truncated=bool(evicted_images),
                estimated_tokens_before=before,
                estimated_tokens_after=after_images,
                kept_item_ids=item_ids,
                reason=(
                    "preserve_history"
                    if request.preserve_history
                    else "image_window"
                    if evicted_images
                    else "under_budget"
                ),
                options=self.options,
            )

        # Fall back to trimming complete interaction blocks only if image eviction is insufficient.
        blocks = _build_blocks(image_request.items)
        protected = _protected_blocks(blocks, self._budget)
        kept = set(range(len(blocks)))
        after = after_images

        # Remove oldest complete blocks; never split a tool call from its result.
        for index in range(len(blocks)):
            if after <= self._budget.target_tokens:
                break
            if index in protected:
                continue
            kept.remove(index)
            after = estimate_tokens(
                _request_with_blocks(image_request, blocks, kept).body
            )

        effective = _request_with_blocks(image_request, blocks, kept)
        after = estimate_tokens(effective.body)
        if after > self._budget.target_tokens:
            raise ContextBudgetExceeded(
                "pinned context exceeds target_estimated_input_tokens: "
                f"estimated={after}, target={self._budget.target_tokens}"
            )

        kept_ids = tuple(
            item_id
            for index, block in enumerate(blocks)
            if index in kept
            for item_id in block.item_ids
        )
        kept_set = set(kept_ids)
        dropped_ids = tuple(item_id for item_id in item_ids if item_id not in kept_set)
        return ContextDecision(
            request=effective,
            policy=self.name,
            version=self.version,
            truncated=True,
            estimated_tokens_before=before,
            estimated_tokens_after=after,
            kept_item_ids=kept_ids,
            dropped_item_ids=dropped_ids,
            reason=("image_window+token_budget" if evicted_images else "token_budget"),
            options=self.options,
        )


def _evict_old_images(
    request: ContextRequest,
    *,
    keep_n: int,
    batch_size: int,
) -> tuple[ContextRequest, int]:
    image_count = sum(
        _count_images(item.get("output"))
        for item in request.items
        if isinstance(item, dict) and item.get("type") in _OUTPUT_TYPES
    )

    # Keep the newest keep_n images and advance eviction only by complete batch_size groups.
    # Stateless calculation gives deterministic results when the same request is retried.
    evict_count = max(0, image_count - keep_n) // batch_size * batch_size
    if evict_count == 0:
        return request, 0

    # Copy only image nodes in tool outputs; preserve user images, calls, results, and adjacent text.
    items = copy.deepcopy(list(request.items))
    call_names = {
        item.get("call_id"): str(item.get("name") or "tool").split("__")[-1]
        for item in items
        if isinstance(item, dict)
        and item.get("type") in _CALL_TYPES
        and isinstance(item.get("call_id"), str)
    }
    remaining = evict_count
    for item in items:
        if remaining == 0:
            break
        if not isinstance(item, dict) or item.get("type") not in _OUTPUT_TYPES:
            continue
        call_id = item.get("call_id")
        tool_name = call_names.get(call_id, "tool")
        output, replaced = _replace_images(
            item.get("output"),
            limit=remaining,
            tool_name=tool_name,
            call_id=call_id,
        )
        item["output"] = output
        remaining -= replaced

    return request.with_items(items), evict_count - remaining


def _count_images(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("type") in _IMAGE_TYPES)
    if not isinstance(value, list):
        return 0
    return sum(
        1
        for block in value
        if isinstance(block, dict) and block.get("type") in _IMAGE_TYPES
    )


def _replace_images(
    value: Any,
    *,
    limit: int,
    tool_name: str,
    call_id: Any,
) -> tuple[Any, int]:
    if limit == 0:
        return value, 0

    image_index = 0

    def placeholder() -> dict[str, str]:
        nonlocal image_index
        image_index += 1
        # Stable text placeholders preserve image positions and support text-only providers.
        return {
            "type": "input_text",
            "text": (
                f"[image omitted: tool={tool_name}, call_id={call_id}, "
                f"image={image_index}]"
            ),
        }

    if isinstance(value, dict) and value.get("type") in _IMAGE_TYPES:
        return placeholder(), 1
    if not isinstance(value, list):
        return value, 0

    replaced = 0
    output: list[Any] = []
    for block in value:
        if (
            replaced < limit
            and isinstance(block, dict)
            and block.get("type") in _IMAGE_TYPES
        ):
            output.append(placeholder())
            replaced += 1
        else:
            output.append(block)
    return output, replaced


def _build_blocks(items: tuple[Any, ...]) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    current: list[tuple[int, Any]] = []
    pending_calls: set[str] = set()
    saw_output = False

    def flush() -> None:
        nonlocal current, pending_calls, saw_output
        if not current:
            return
        values = tuple(value for _, value in current)
        blocks.append(
            ContextBlock(
                item_ids=tuple(f"item_{index:04d}" for index, _ in current),
                items=values,
                kind=_block_kind(values),
                pinned=bool(pending_calls),
                observation=_is_observation(values),
            )
        )
        current = []
        pending_calls = set()
        saw_output = False

    for index, item in enumerate(items):
        item_type = item.get("type") if isinstance(item, dict) else None
        role = item.get("role") if isinstance(item, dict) else None

        if item_type == "additional_tools" or (
            item_type == "message" and role in {"system", "developer", "user"}
        ):
            flush()
            blocks.append(
                ContextBlock(
                    item_ids=(f"item_{index:04d}",),
                    items=(item,),
                    kind="metadata" if item_type == "additional_tools" else str(role),
                    pinned=item_type == "additional_tools"
                    or role in {"system", "developer"},
                    observation=False,
                )
            )
            continue

        if item_type in _CALL_TYPES:
            if current and saw_output and not pending_calls:
                flush()
            current.append((index, item))
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                pending_calls.add(call_id)
            continue

        if item_type in _OUTPUT_TYPES:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or call_id not in pending_calls:
                raise ValueError(f"tool output has no matching call: {call_id!r}")
            current.append((index, item))
            pending_calls.remove(call_id)
            saw_output = True
            continue

        if current and saw_output and not pending_calls:
            flush()
        current.append((index, item))

    flush()
    return blocks


def _protected_blocks(blocks: list[ContextBlock], budget: ContextBudget) -> set[int]:
    protected = {index for index, block in enumerate(blocks) if block.pinned}

    user_blocks = [index for index, block in enumerate(blocks) if block.kind == "user"]
    if user_blocks:
        protected.add(user_blocks[0])
        protected.add(user_blocks[-1])

    history = [
        index
        for index, block in enumerate(blocks)
        if block.kind not in {"metadata", "system", "developer", "user"}
    ]
    protected.update(history[-budget.keep_recent_blocks :] if budget.keep_recent_blocks else ())

    observations = [index for index, block in enumerate(blocks) if block.observation]
    protected.update(
        observations[-budget.keep_observation_blocks :]
        if budget.keep_observation_blocks
        else ()
    )
    return protected


def _request_with_blocks(
    request: ContextRequest,
    blocks: list[ContextBlock],
    kept: set[int],
) -> ContextRequest:
    items = [
        item
        for index, block in enumerate(blocks)
        if index in kept
        for item in block.items
    ]
    return request.with_items(items)


def _block_kind(items: tuple[Any, ...]) -> str:
    if any(
        isinstance(item, dict) and item.get("type") in _CALL_TYPES for item in items
    ):
        return "tool_turn"
    return "assistant_turn"


def _is_observation(items: tuple[Any, ...]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").split("__")[-1]
        if name in _OBSERVATION_NAMES:
            return True
        if "input_image" in json.dumps(item, ensure_ascii=False, default=str):
            return True
    return False
