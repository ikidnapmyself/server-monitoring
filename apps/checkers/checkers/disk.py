"""
Disk space checker.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


class DiskChecker(BaseChecker):
    """
    Check disk space usage for specified mount points.

    Measures disk utilization and compares against thresholds.
    Default thresholds: warning at 80%, critical at 95%.
    """

    name = "disk"
    warning_threshold = 80.0
    critical_threshold = 95.0

    def __init__(self, paths: list[str] | None = None, **kwargs) -> None:
        """
        Initialize disk checker.

        Args:
            paths: List of mount points to check (default: ["/"]).
            **kwargs: Additional BaseChecker arguments.
        """
        super().__init__(**kwargs)
        self.paths = paths or ["/"]

    def check(self) -> CheckResult:
        """
        Check disk usage for all configured paths.

        Returns:
            CheckResult with disk usage metrics. Status reflects the worst path.
        """
        try:
            worst_status = CheckStatus.OK
            worst_percent = 0.0
            worst_path = ""
            disk_metrics = {}

            for path in self.paths:
                try:
                    usage = psutil.disk_usage(path)
                    percent = usage.percent

                    disk_metrics[path] = {
                        "percent": percent,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                    }

                    path_status = self._determine_status(percent)

                    # Track the worst status
                    if percent > worst_percent:
                        worst_percent = percent
                        worst_status = path_status
                        worst_path = path

                except FileNotFoundError:
                    disk_metrics[path] = {"error": "Path not found"}  # type: ignore[dict-item]
                    worst_status = CheckStatus.UNKNOWN
                    worst_path = path

            metrics = {
                "disks": disk_metrics,
                "worst_percent": worst_percent,
                "worst_path": worst_path,
            }

            if worst_status == CheckStatus.UNKNOWN:
                message = f"Disk check error: path '{worst_path}' not found"
            else:
                message = f"Disk usage: {worst_path} at {worst_percent:.1f}%"

            return self._make_result(status=worst_status, message=message, metrics=metrics)

        except Exception as e:
            return self._error_result(str(e))
