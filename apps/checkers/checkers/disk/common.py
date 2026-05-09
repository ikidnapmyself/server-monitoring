"""Cross-platform disk analysis checker."""

import os

from apps.checkers.checkers.disk.base import BaseDiskAnalyzer


class DiskCommonChecker(BaseDiskAnalyzer):
    """Disk cleanup analysis on any Unix platform."""

    name = "disk_common"

    scan_targets = ["/var/log", "~/.cache"]
    old_file_targets = ["/tmp", "/var/tmp"]
    large_file_targets = ["~"]
    old_max_age_days = 7

    recommendation_rules = [
        (["/var/log"], "Compress or rotate old log files in /var/log"),
        (["pip"], "Run 'pip cache purge' to clear pip cache"),
        (["npm", ".npm"], "Run 'npm cache clean --force' to clear npm cache"),
        ([".cache"], "Clear user caches in ~/.cache"),
    ]
    old_files_advice = "Remove old temporary files from /tmp and /var/tmp"
    large_files_advice = "Review and remove large files in home directory"

    def _is_applicable(self) -> bool:
        return os.name == "posix"
