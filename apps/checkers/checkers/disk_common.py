"""Cross-platform disk analysis checker."""

import os
import sys

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.disk_utils import (
    dir_size,
    find_large_files,
    find_old_files,
    scan_directory,
)


class DiskCommonChecker(BaseChecker):
    """Analyze disk usage on any Unix platform."""

    name = "disk_common"
    warning_threshold = 5000.0
    critical_threshold = 20000.0

    def check(self) -> CheckResult:
        # Skip on non-Unix platforms (e.g., Windows)
        if os.name != "posix":
            return self._make_result(
                status=CheckStatus.OK,
                message="Skipped: not applicable for this platform",
                metrics={"platform": sys.platform},
            )
        try:
            scan_targets = ["/var/log", "~/.cache"]
            old_file_targets = ["/tmp", "/var/tmp"]
            large_file_targets = ["~"]

            space_hogs = []
            seen = set()
            for target in scan_targets:
                path = os.path.expanduser(target)
                for item in scan_directory(path, timeout=self.timeout):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        space_hogs.append(item)

            old_files = []
            for target in old_file_targets:
                path = os.path.expanduser(target)
                for item in find_old_files(path, timeout=self.timeout):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        old_files.append(item)

            # Build set of already-scanned paths to exclude from large file walk
            # This prevents double-counting (e.g., ~/.cache files counted both as space_hogs and large_files)
            exclude_paths = {os.path.expanduser(t) for t in scan_targets}
            
            large_files = []
            for target in large_file_targets:
                path = os.path.expanduser(target)
                for item in find_large_files(path, timeout=self.timeout, exclude_paths=exclude_paths):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        large_files.append(item)

            total = (
                sum(h["size_mb"] for h in space_hogs)
                + sum(f["size_mb"] for f in old_files)
                + sum(f["size_mb"] for f in large_files)
            )
            recs = self._build_recommendations(space_hogs, old_files, large_files)
            status = self._determine_status(total)

            return self._make_result(
                status=status,
                message=f"Disk analysis: {total:.1f} MB recoverable",
                metrics={
                    "platform": sys.platform,
                    "space_hogs": space_hogs,
                    "old_files": old_files,
                    "large_files": large_files,
                    "total_recoverable_mb": total,
                    "recommendations": recs,
                },
            )
        except Exception as e:
            return self._error_result(str(e))

    def _build_recommendations(self, space_hogs, old_files, large_files):
        """Generate cross-platform cleanup recommendations."""
        recs = []
        paths = [h["path"] for h in space_hogs]
        if any("/var/log" in p for p in paths):
            recs.append("Compress or rotate old log files in /var/log")
        if any("pip" in p for p in paths):
            recs.append("Run 'pip cache purge' to clear pip cache")
        if any("npm" in p or ".npm" in p for p in paths):
            recs.append("Run 'npm cache clean --force' to clear npm cache")
        if any(".cache" in p for p in paths):
            recs.append("Clear user caches in ~/.cache")
        if old_files:
            recs.append("Remove old temporary files from /tmp and /var/tmp")
        if large_files:
            recs.append("Review and remove large files in home directory")
        return recs
