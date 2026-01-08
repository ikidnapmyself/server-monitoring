"""
Memory usage checker.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult


class MemoryChecker(BaseChecker):
    """
    Check memory (RAM) usage percentage.

    Measures virtual memory utilization and compares against thresholds.
    Default thresholds: warning at 70%, critical at 90%.
    """

    name = "memory"
    warning_threshold = 70.0
    critical_threshold = 90.0

    def __init__(self, include_swap: bool = False, **kwargs) -> None:
        """
        Initialize memory checker.

        Args:
            include_swap: If True, also report swap usage.
            **kwargs: Additional BaseChecker arguments.
        """
        super().__init__(**kwargs)
        self.include_swap = include_swap

    def check(self) -> CheckResult:
        """
        Check memory usage.

        Returns:
            CheckResult with memory usage metrics.
        """
        try:
            mem = psutil.virtual_memory()
            mem_percent = mem.percent

            metrics = {
                "memory_percent": mem_percent,
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_used_gb": round(mem.used / (1024**3), 2),
                "memory_available_gb": round(mem.available / (1024**3), 2),
            }

            if self.include_swap:
                swap = psutil.swap_memory()
                metrics.update(
                    {
                        "swap_percent": swap.percent,
                        "swap_total_gb": round(swap.total / (1024**3), 2),
                        "swap_used_gb": round(swap.used / (1024**3), 2),
                    }
                )

            status = self._determine_status(mem_percent)
            message = f"Memory usage: {mem_percent:.1f}% ({metrics['memory_used_gb']:.1f}/{metrics['memory_total_gb']:.1f} GB)"

            return self._make_result(status=status, message=message, metrics=metrics)

        except Exception as e:
            return self._error_result(str(e))

