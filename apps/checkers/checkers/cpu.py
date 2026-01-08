"""
CPU usage checker.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult


class CPUChecker(BaseChecker):
    """
    Check CPU usage percentage.

    Measures CPU utilization and compares against warning/critical thresholds.
    Default thresholds: warning at 70%, critical at 90%.
    """

    name = "cpu"
    warning_threshold = 70.0
    critical_threshold = 90.0

    def __init__(
        self,
        interval: float = 1.0,
        per_cpu: bool = False,
        **kwargs,
    ) -> None:
        """
        Initialize CPU checker.

        Args:
            interval: Seconds to sample CPU usage (default 1.0).
            per_cpu: If True, check per-CPU usage; otherwise system-wide.
            **kwargs: Additional BaseChecker arguments.
        """
        super().__init__(**kwargs)
        self.interval = interval
        self.per_cpu = per_cpu

    def check(self) -> CheckResult:
        """
        Check CPU usage.

        Returns:
            CheckResult with CPU usage metrics.
        """
        try:
            if self.per_cpu:
                cpu_percents = psutil.cpu_percent(interval=self.interval, percpu=True)
                cpu_percent = max(cpu_percents)  # Use highest core for status
                metrics = {
                    "cpu_percent": cpu_percent,
                    "per_cpu_percent": cpu_percents,
                    "cpu_count": len(cpu_percents),
                }
            else:
                cpu_percent = psutil.cpu_percent(interval=self.interval)
                metrics = {
                    "cpu_percent": cpu_percent,
                    "cpu_count": psutil.cpu_count(),
                }

            status = self._determine_status(cpu_percent)
            message = f"CPU usage: {cpu_percent:.1f}%"

            return self._make_result(status=status, message=message, metrics=metrics)

        except Exception as e:
            return self._error_result(str(e))

