"""Linux disk analysis checker."""

import sys

from apps.checkers.checkers.disk.base import BaseDiskAnalyzer


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
        (["apt"], ["Run 'sudo apt clean' to clear APT package cache"]),
        (["journal"], ["Run 'sudo journalctl --vacuum-size=100M' to trim journal logs"]),
        (["docker"], ["Run 'docker system prune' to clean unused Docker data"]),
        (["snap"], ["Remove old snap package revisions"]),
    ]
    old_files_advice = "Remove old temporary files from /tmp"
    large_files_advice = "Review and remove large files in /srv and /opt"

    def _is_applicable(self) -> bool:
        return sys.platform == "linux"
