"""macOS disk analysis checker."""

import sys

from apps.checkers.checkers.disk.base import BaseDiskAnalyzer


class DiskMacOSChecker(BaseDiskAnalyzer):
    """Disk cleanup analysis on macOS."""

    name = "disk_macos"

    scan_targets = [
        "~/Library/Caches",
        "/Library/Caches",
        "~/Library/Logs",
        "~/Library/Developer/Xcode/DerivedData",
    ]
    old_file_targets = ["~/Downloads"]
    large_file_targets = ["/Library/Application Support", "/Users/Shared"]
    old_max_age_days = 30

    recommendation_rules = [
        (["Homebrew"], "Run 'brew cleanup --prune=all' to free Homebrew cache"),
        (
            ["DerivedData", "Xcode"],
            "Remove ~/Library/Developer/Xcode/DerivedData to free build cache",
        ),
        (["Caches"], "Clear application caches in ~/Library/Caches"),
    ]
    old_files_advice = "Remove old files from ~/Downloads"
    large_files_advice = (
        "Review and remove large files in /Library/Application Support and /Users/Shared"
    )

    def _is_applicable(self) -> bool:
        return sys.platform == "darwin"
