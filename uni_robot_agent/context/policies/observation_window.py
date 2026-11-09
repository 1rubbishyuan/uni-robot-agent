"""Bound old visual observations while retaining decisions and action history."""
from copy import deepcopy

from uni_robot_agent.context.history import ContextDecision, register_context_policy
from uni_robot_agent.context.observation_history import estimate_context, observations


@register_context_policy("observation_window")
class ObservationWindowPolicy:
    name = "observation_window"
    version = "1"

    def __init__(self, *, observation_horizon=2, keep_recent_blocks=4,
                 max_estimated_input_tokens=None, image_token_allowance=1536):
        if observation_horizon < 1 or keep_recent_blocks < 0 or image_token_allowance < 1:
            raise ValueError("Observation horizon/image allowance must be positive; recent blocks nonnegative")
        if max_estimated_input_tokens is not None and max_estimated_input_tokens < 1:
            raise ValueError("Maximum estimated input tokens must be positive")
        self.horizon = observation_horizon
        self.recent = keep_recent_blocks
        self.maximum = max_estimated_input_tokens
        self.image_allowance = image_token_allowance

    @property
    def options(self):
        return {"observation_horizon": self.horizon, "keep_recent_blocks": self.recent,
                "max_estimated_input_tokens": self.maximum,
                "image_token_allowance": self.image_allowance}

    def select(self, request):
        if request.body.get("previous_response_id"):
            raise ValueError("observation_window requires explicit history, not previous_response_id")
        before = estimate_context(request.body, tokens_per_image=self.image_allowance)
        item_ids = tuple(f"item_{index:04d}" for index in range(len(request.items)))
        # Native compaction must see the full history, including original images.
        if request.preserve_history:
            return ContextDecision(request=request, policy=self.name, version=self.version,
                                   truncated=False, estimated_tokens_before=before["text"]+before["images"],
                                   estimated_tokens_after=before["text"]+before["images"],
                                   kept_item_ids=item_ids, reason="preserve_history", options=self.options)
        items = deepcopy(list(request.items))
        views = observations(items, request.wire_format)
        # Last occurrence wins if multiple results refer to the same immutable snapshot.
        groups = list(dict.fromkeys(view.key for view in reversed(views)))
        protected = set(groups[:self.horizon]) | {view.key for view in views if view.pinned}
        recent = set(groups[:self.recent])
        evicted = []
        for view in views:
            if view.key not in protected:
                view.evict(compact_metadata=view.key not in recent)
                evicted.append(view.call_id)
        effective = request.with_items(items) if evicted else request
        after = estimate_context(effective.body, tokens_per_image=self.image_allowance)
        total = after["text"]+after["images"]
        if self.maximum is not None and total > self.maximum:
            raise ValueError("Protected history exceeds observation_window budget; use native compaction "
                             f"or increase the budget (estimated={total}, maximum={self.maximum})")
        return ContextDecision(request=effective, policy=self.name, version=self.version,
                               truncated=bool(evicted), estimated_tokens_before=before["text"]+before["images"],
                               estimated_tokens_after=total, kept_item_ids=item_ids,
                               reason="observation_window" if evicted else "under_window",
                               options={**self.options, "before": before, "after": after,
                                        "evicted_observation_call_ids": evicted})
