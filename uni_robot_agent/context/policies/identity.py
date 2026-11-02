"""Identity policy that leaves provider requests unchanged."""

from __future__ import annotations

from uni_robot_agent.context.history import (
    ContextDecision,
    ContextRequest,
    estimate_tokens,
    register_context_policy,
)


@register_context_policy("identity")
class IdentityContextPolicy:
    name = "identity"
    version = "1"

    @property
    def options(self) -> dict:
        return {}

    def select(self, request: ContextRequest) -> ContextDecision:
        tokens = estimate_tokens(request.body)
        ids = tuple(f"item_{index:04d}" for index, _ in enumerate(request.items))
        return ContextDecision(
            request=request,
            policy=self.name,
            version=self.version,
            truncated=False,
            estimated_tokens_before=tokens,
            estimated_tokens_after=tokens,
            kept_item_ids=ids,
            reason="identity",
        )
