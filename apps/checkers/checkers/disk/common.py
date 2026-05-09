"""Cross-platform disk analysis checker."""

import os

from apps.checkers.checkers.disk.base import BaseDiskAnalyzer
from apps.checkers.checkers.disk.recommendations import (
    CARGO,
    COMPOSER,
    GO_MODULES,
    GRADLE,
    LOG_ROTATE,
    MAVEN,
    NPM,
    PIP,
    PNPM,
    USER_CACHE,
    YARN,
)


class DiskCommonChecker(BaseDiskAnalyzer):
    """Disk cleanup analysis on any Unix platform."""

    name = "disk_common"

    scan_targets = ["/var/log", "~/.cache"]
    old_file_targets = ["/tmp", "/var/tmp"]
    large_file_targets = ["~"]
    old_max_age_days = 7

    recommendation_rules = [
        LOG_ROTATE,
        PIP,
        NPM,
        YARN,
        PNPM,
        COMPOSER,
        GRADLE,
        MAVEN,
        CARGO,
        GO_MODULES,
        USER_CACHE,
    ]
    old_files_advice = "Remove old temporary files from /tmp and /var/tmp"
    large_files_advice = "Review and remove large files in home directory"

    def _is_applicable(self) -> bool:
        return os.name == "posix"
