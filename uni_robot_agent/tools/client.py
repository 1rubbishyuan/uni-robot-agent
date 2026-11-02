"""Python calls and job handles over the shared execution contract."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from uni_robot_agent.execution import ExecutionResult, ToolExecutor
from uni_robot_agent.tools.toolkit import ToolOutput


def _validate_timeout(timeout: float | None) -> None:
    if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
        raise ValueError("timeout must be finite and nonnegative")


class ToolJob:
    """Handle for one tool submission, with result polling, waiting and cancellation."""

    def __init__(self, executor: ToolExecutor, receipt: ExecutionResult):
        self._executor = executor
        self._receipt = receipt

    @property
    def job_id(self) -> str | None:
        return self._receipt.job_id

    def poll(self) -> ExecutionResult:
        return self._executor.job_result(self.job_id) if self.job_id else self._receipt

    def result(self, timeout: float | None = None) -> ToolOutput:
        _validate_timeout(timeout)
        result = self._executor.job_result(self.job_id, timeout) if self.job_id else self._receipt
        if result.output is None:
            raise TimeoutError(f"Tool job {self.job_id} is still {result.status}; it was not cancelled")
        return result.output

    def cancel(self) -> bool:
        return self._executor.cancel_job(self.job_id) if self.job_id else False


class ToolClient:
    """Discover and invoke only the tools exposed by this session."""

    def __init__(self, executor: ToolExecutor, require_active: Callable[[], None]):
        self._executor = executor
        self._require_active = require_active

    def list(self) -> list[dict[str, Any]]:
        self._require_active()
        return self._executor.get_tool_schemas()

    def submit(self, name: str, arguments: dict[str, Any] | None = None) -> ToolJob:
        self._require_active()
        return ToolJob(self._executor, self._executor.submit(name, dict(arguments or {})))

    def call(self, name: str, arguments: dict[str, Any] | None = None, *,
             timeout: float | None = None) -> ToolOutput:
        _validate_timeout(timeout)
        return self.submit(name, arguments).result(timeout)
