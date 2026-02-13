"""Linux disk analysis checker."""

import sys

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.disk_utils import (
    find_old_files,
    scan_directory,
)


class DiskLinuxChecker(BaseChecker):
    """Analyze disk usage on Linux systems."""

    name = "disk_linux"
    warning_threshold = 5000.0
    critical_threshold = 20000.0

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._make_result(
                status=CheckStatus.OK,
                message="Skipped: not applicable for this platform",
                metrics={"platform": sys.platform},
            )

        try:
            scan_targets = [
                "/var/cache/apt/archives",
                "/var/log/journal",
                "/var/lib/docker",
                "/var/lib/snapd",
            ]
            old_file_targets = ["/tmp"]

            space_hogs = []
            seen = set()
            for target in scan_targets:
                for item in scan_directory(target, timeout=self.timeout):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        space_hogs.append(item)

            old_files = []
            for target in old_file_targets:
                for item in find_old_files(target, max_age_days=7, timeout=self.timeout):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        old_files.append(item)

            total = sum(h["size_mb"] for h in space_hogs) + sum(f["size_mb"] for f in old_files)
            recs = self._build_recommendations(space_hogs, old_files)
            status = self._determine_status(total)

            return self._make_result(
                status=status,
                message=f"Disk analysis: {total:.1f} MB recoverable",
                metrics={
                    "platform": sys.platform,
                    "space_hogs": space_hogs,
                    "old_files": old_files,
                    "total_recoverable_mb": total,
                    "recommendations": recs,
                },
            )
        except Exception as e:
            return self._error_result(str(e))

    def _build_recommendations(self, space_hogs, old_files):
        """Generate Linux-specific cleanup recommendations."""
        recs = []
        paths = [h["path"] for h in space_hogs]
        if any("apt" in p for p in paths):
            recs.append("Run 'sudo apt clean' to clear APT package cache")
        if any("journal" in p for p in paths):
            recs.append("Run 'sudo journalctl --vacuum-size=100M' to trim journal logs")
        if any("docker" in p for p in paths):
            recs.append("Run 'docker system prune' to clean unused Docker data")
        if any("snap" in p for p in paths):
            recs.append("Remove old snap package revisions")
        if old_files:
            recs.append("Remove old temporary files from /tmp")
        return recs
