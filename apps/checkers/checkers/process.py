"""
Process/service status checker.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


class ProcessChecker(BaseChecker):
    """
    Check if specified processes are running.

    Verifies that named processes are running on the system.
    Status is based on the percentage of required processes that are running.
    Default thresholds: warning if <100% running, critical if <50% running.
    """

    name = "process"
    warning_threshold = 100.0  # All processes must be running for OK
    critical_threshold = 50.0  # At least 50% must be running to avoid CRITICAL

    def __init__(self, processes: list[str] | None = None, **kwargs) -> None:
        """
        Initialize process checker.

        Args:
            processes: List of process names to check (e.g., ["nginx", "postgres"]).
            **kwargs: Additional BaseChecker arguments.
        """
        super().__init__(**kwargs)
        self.processes = processes or []

    def _is_process_running(self, name: str) -> dict:
        """
        Check if a process with the given name is running.

        Args:
            name: Process name to search for.

        Returns:
            Dict with process info if found, or error status.
        """
        name_lower = name.lower()
        try:
            for proc in psutil.process_iter(
                ["pid", "name", "status", "cpu_percent", "memory_percent"]
            ):
                try:
                    proc_name = proc.info["name"].lower()
                    if name_lower in proc_name or proc_name in name_lower:
                        return {
                            "running": True,
                            "pid": proc.info["pid"],
                            "status": proc.info["status"],
                            "cpu_percent": proc.info.get("cpu_percent"),
                            "memory_percent": proc.info.get("memory_percent"),
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        return {"running": False, "pid": None, "status": "not_found"}

    def check(self) -> CheckResult:
        """
        Check if all configured processes are running.

        Returns:
            CheckResult with process status for each configured process.
        """
        if not self.processes:
            return self._make_result(
                status=CheckStatus.OK,
                message="No processes configured to monitor",
                metrics={"processes": {}, "running_count": 0, "total_count": 0},
            )

        try:
            results = {}
            running_count = 0

            for process_name in self.processes:
                info = self._is_process_running(process_name)
                results[process_name] = info
                if info["running"]:
                    running_count += 1

            total_count = len(self.processes)
            running_percent = (running_count / total_count * 100) if total_count > 0 else 0

            metrics = {
                "processes": results,
                "running_count": running_count,
                "total_count": total_count,
                "running_percent": running_percent,
            }

            # Determine status
            if running_percent >= self.warning_threshold:
                status = CheckStatus.OK
            elif running_percent >= self.critical_threshold:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.CRITICAL

            # Build message with missing processes
            missing = [name for name, info in results.items() if not info["running"]]
            if missing:
                message = f"Processes: {running_count}/{total_count} running. Missing: {', '.join(missing)}"
            else:
                message = f"Processes: {running_count}/{total_count} running"

            return self._make_result(status=status, message=message, metrics=metrics)

        except Exception as e:
            return self._error_result(str(e))
