"""Disk inode-usage checker (``os.statvfs``).

A filesystem can exhaust its inodes — many tiny files — while free space
remains, which the ``disk`` space checker will not catch. Cross-platform Unix
via ``os.statvfs`` (Linux and macOS); skips as OK where ``statvfs`` is
unavailable (e.g. Windows). Mirrors ``DiskChecker``: the worst path drives the
status.

See docs/plans/2026-08-07-thermal-io-checkers-design.md (sibling checkers).
"""

import os
from typing import Any

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from config.describing import Action, Description, format_percent


class DiskInodesChecker(BaseChecker):
    """Check inode usage per filesystem; worst path drives the status."""

    name = "disk_inodes"
    label = "Inode usage"
    problem = "Running out of inodes"
    warning_threshold = 80.0
    critical_threshold = 95.0

    def __init__(self, paths: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.paths = paths or ["/"]

    def check(self) -> CheckResult:
        if not hasattr(os, "statvfs"):
            return self._make_result(
                status=CheckStatus.SKIPPED,
                message="Skipped: statvfs unavailable",
                metrics=self._empty_metrics(),
            )

        try:
            return self._check_statvfs()
        except Exception as e:  # pragma: no cover - defensive, mirrors DiskChecker
            return self._error_result(str(e))

    def _check_statvfs(self) -> CheckResult:
        worst_status = CheckStatus.OK
        worst_percent = 0.0
        worst_path = ""
        error_path = ""
        fs_metrics: dict = {}

        for path in self.paths:
            try:
                st = os.statvfs(path)
            except (FileNotFoundError, PermissionError) as exc:
                fs_metrics[path] = {"error": str(exc)}
                if not error_path:
                    error_path = path
                continue

            total = st.f_files
            if total <= 0:
                # Some filesystems (tmpfs/overlay) do not track inodes.
                fs_metrics[path] = {"inodes_supported": False}
                continue

            used = total - st.f_ffree
            percent = used / total * 100
            fs_metrics[path] = {
                "percent": round(percent, 1),
                "total": total,
                "used": used,
                "free": st.f_ffree,
            }
            if not worst_path or percent > worst_percent:
                worst_percent = percent
                worst_status = self._determine_status(percent)
                worst_path = path

        metrics = {
            "filesystems": fs_metrics,
            "worst_percent": round(worst_percent, 1),
            "worst_path": worst_path,
        }

        if error_path:
            return self._make_result(
                status=CheckStatus.UNKNOWN,
                message=f"Inode check error: path '{error_path}' not accessible",
                metrics=metrics,
            )

        if not worst_path:
            return self._make_result(
                status=CheckStatus.OK,
                message="Inode usage: no inode-tracking filesystems",
                metrics=metrics,
            )

        message = f"Inode usage: {worst_path} at {worst_percent:.1f}%"
        return self._make_result(status=worst_status, message=message, metrics=metrics)

    def _empty_metrics(self) -> dict:
        return {"filesystems": {}, "worst_percent": 0.0, "worst_path": ""}

    def describe_result(self, status: CheckStatus, metrics: dict[str, Any]) -> Description:
        path = metrics["worst_path"]
        if not path:
            return self._describe(status, "No filesystems track inodes", metrics)
        percent = format_percent(metrics["worst_percent"])
        if status is CheckStatus.OK:
            return self._describe(
                status, f"Fullest filesystem {path} uses {percent} of its inodes", metrics
            )
        return self._describe(
            status,
            f"{path} uses {percent} of its inodes",
            metrics,
            action=Action(
                f"Find directories under {path} holding many small files "
                "and remove what isn't needed."
            ),
        )
