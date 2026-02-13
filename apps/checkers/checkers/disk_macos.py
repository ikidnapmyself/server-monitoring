"""macOS disk analysis checker."""

import os
import sys

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.disk_utils import (
    find_old_files,
    scan_directory,
)


class DiskMacOSChecker(BaseChecker):
    """Analyze disk usage on macOS systems."""

    name = "disk_macos"
    warning_threshold = 5000.0
    critical_threshold = 20000.0

    def check(self) -> CheckResult:
        if sys.platform != "darwin":
            return self._make_result(
                status=CheckStatus.OK,
                message="Skipped: not applicable for this platform",
                metrics={"platform": sys.platform},
            )

        try:
            scan_targets = [
                "~/Library/Caches",
                "/Library/Caches",
                "~/Library/Logs",
                "~/Library/Developer/Xcode/DerivedData",
            ]
            old_file_targets = ["~/Downloads"]

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
                for item in find_old_files(path, max_age_days=30, timeout=self.timeout):
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
        """Generate macOS-specific cleanup recommendations."""
        recs = []
        paths = [h["path"] for h in space_hogs]
        if any("Homebrew" in p for p in paths):
            recs.append("Run 'brew cleanup --prune=all' to free Homebrew cache")
        if any("DerivedData" in p or "Xcode" in p for p in paths):
            recs.append("Remove ~/Library/Developer/Xcode/DerivedData to free build cache")
        if any("Caches" in p for p in paths):
            recs.append("Clear application caches in ~/Library/Caches")
        if old_files:
            recs.append("Remove old files from ~/Downloads")
        return recs
