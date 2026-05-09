"""Linux disk analysis checker."""

import sys

from apps.checkers.checkers.disk.base import BaseDiskAnalyzer
from apps.checkers.checkers.disk.recommendations import (
    APT,
    DOCKER,
    JETBRAINS,
    JOURNAL,
    SNAP,
)


class DiskLinuxChecker(BaseDiskAnalyzer):
    """Disk cleanup analysis on Linux."""

    name = "disk_linux"

    scan_targets = [
        "/var/cache/apt/archives",
        "/var/log/journal",
        "/var/lib/docker",
        "/var/lib/snapd",
    ]
    old_file_targets = ["/tmp"]
    large_file_targets = ["/srv", "/opt"]
    old_max_age_days = 7

    recommendation_rules = [
        APT,
        JOURNAL,
        DOCKER,
        SNAP,
        JETBRAINS,
    ]
    old_files_advice = "Remove old temporary files from /tmp"
    large_files_advice = "Review and remove large files in /srv and /opt"

    def _is_applicable(self) -> bool:
        return sys.platform == "linux"
