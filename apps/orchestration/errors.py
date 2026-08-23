"""Orchestration failures shared by the orchestrator and its executors.

``StageExecutionError`` lives here rather than in ``orchestrator.py`` because
that module imports ``executors``: an executor that needs to raise the error
could not import it at module level without a cycle.
"""

from __future__ import annotations


class StageExecutionError(Exception):
    """Exception raised when a stage fails execution."""

    def __init__(self, stage: str, errors: list[str], retryable: bool = True):
        self.stage = stage
        self.errors = errors
        self.retryable = retryable
        super().__init__(f"Stage {stage} failed: {'; '.join(errors)}")


def routing_gap(stage: str, code: str, detail: str) -> StageExecutionError:
    """The routing table does not say where this work goes. Never retryable.

    Three shapes of the same failure: ``no_route`` (no lane matched the alert),
    ``no_channel`` (the matched lane names no active channel) and ``no_driver``
    (the lane's channel names a driver that is not registered). None can be
    fixed by trying again — the alert is stuck until an operator edits a row, and
    a retryable failure would spin forever. One factory so that reasoning is
    stated once and the three cannot drift apart.

    ``code`` leads the message because operators and log searches key on it.
    """
    return StageExecutionError(stage=stage, errors=[f"{code}: {detail}"], retryable=False)
