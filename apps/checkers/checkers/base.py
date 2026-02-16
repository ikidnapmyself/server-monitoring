"""
Base checker classes and result types for server monitoring.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CheckStatus(Enum):
    """Status levels for check results."""

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"  # For errors or unreachable states


@dataclass
class CheckResult:
    """
    Standardized result from a checker.

    Attributes:
        status: The status level of the check (OK, WARNING, CRITICAL, UNKNOWN).
        message: Human-readable description of the result.
        metrics: Dictionary of measured values (e.g., {"cpu_percent": 45.2}).
        checker_name: Name of the checker that produced this result.
        error: Optional error message if the check failed.
    """

    status: CheckStatus
    message: str
    metrics: dict[str, Any] = field(default_factory=dict)
    checker_name: str = ""
    error: str | None = None

    def is_ok(self) -> bool:
        """Return True if status is OK."""
        return self.status == CheckStatus.OK

    def is_critical(self) -> bool:
        """Return True if status is CRITICAL."""
        return self.status == CheckStatus.CRITICAL


class BaseChecker(ABC):
    """
    Abstract base class for all server checkers.

    Subclasses must implement the `check()` method and define a `name` attribute.

    Attributes:
        name: Unique identifier for this checker.
        timeout: Maximum seconds to wait for the check to complete.
        warning_threshold: Value at which status becomes WARNING.
        critical_threshold: Value at which status becomes CRITICAL.
        enabled: Whether this checker is enabled (default: True).
    """

    name: str = "base"
    timeout: float = 10.0
    warning_threshold: float = 70.0
    critical_threshold: float = 90.0
    enabled: bool = True

    def __init__(
        self,
        timeout: float | None = None,
        warning_threshold: float | None = None,
        critical_threshold: float | None = None,
        enabled: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the checker with optional threshold overrides.

        Args:
            timeout: Override default timeout in seconds.
            warning_threshold: Override default warning threshold.
            critical_threshold: Override default critical threshold.
            enabled: Override default enabled state.
            **kwargs: Additional checker-specific configuration.
        """
        if timeout is not None:
            self.timeout = timeout
        if warning_threshold is not None:
            self.warning_threshold = warning_threshold
        if critical_threshold is not None:
            self.critical_threshold = critical_threshold
        if enabled is not None:
            self.enabled = enabled

    @abstractmethod
    def check(self) -> CheckResult:
        """
        Perform the health check and return a result.

        Returns:
            CheckResult with status, message, and metrics.
        """
        ...

    def run(self, *, trace_id: str = "") -> CheckResult:
        """
        Run the check, time it, and create a CheckRun audit record.

        Returns:
            CheckResult from check() (or an UNKNOWN result on exception).
        """
        start = time.perf_counter()
        try:
            result = self.check()
        except Exception as exc:
            result = self._error_result(str(exc))
        duration_ms = (time.perf_counter() - start) * 1000

        try:
            from apps.checkers.models import CheckRun

            CheckRun.objects.create(
                checker_name=self.name,
                status=result.status.value,
                message=result.message,
                metrics=result.metrics,
                error=result.error or "",
                warning_threshold=self.warning_threshold,
                critical_threshold=self.critical_threshold,
                duration_ms=duration_ms,
                trace_id=trace_id,
            )
        except Exception:
            logger.warning("Failed to create CheckRun for '%s'", self.name, exc_info=True)

        return result

    def _determine_status(self, value: float) -> CheckStatus:
        """
        Determine status based on value and thresholds.

        Args:
            value: The measured value to evaluate.

        Returns:
            CheckStatus based on threshold comparison.
        """
        if value >= self.critical_threshold:
            return CheckStatus.CRITICAL
        elif value >= self.warning_threshold:
            return CheckStatus.WARNING
        return CheckStatus.OK

    def _make_result(
        self,
        status: CheckStatus,
        message: str,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> CheckResult:
        """
        Create a CheckResult with this checker's name.

        Args:
            status: The status level.
            message: Human-readable message.
            metrics: Optional metrics dictionary.
            error: Optional error message.

        Returns:
            CheckResult instance.
        """
        return CheckResult(
            status=status,
            message=message,
            metrics=metrics or {},
            checker_name=self.name,
            error=error,
        )

    def _error_result(self, error: str) -> CheckResult:
        """
        Create an UNKNOWN status result for errors.

        Args:
            error: Error description.

        Returns:
            CheckResult with UNKNOWN status.
        """
        return self._make_result(
            status=CheckStatus.UNKNOWN,
            message=f"Check failed: {error}",
            error=error,
        )
