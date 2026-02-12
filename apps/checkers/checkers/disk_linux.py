"""Linux disk analysis checker."""

import os
import sys
import time

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


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
                for item in self._scan_directory(target):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        space_hogs.append(item)

            old_files = []
            for target in old_file_targets:
                for item in self._find_old_files(target):
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

    def _scan_directory(self, path: str) -> list[dict]:
        """Scan a directory for subdirectories/files and their sizes."""
        results: list[dict] = []
        if not os.path.isdir(path):
            return results
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            size = self._dir_size(entry.path)
                        else:
                            size = entry.stat(follow_symlinks=False).st_size
                        size_mb = size / (1024 * 1024)
                        if size_mb >= 1.0:
                            results.append({"path": entry.path, "size_mb": round(size_mb, 1)})
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass
        return sorted(results, key=lambda x: x["size_mb"], reverse=True)

    def _find_old_files(self, path: str, max_age_days: int = 7) -> list[dict]:
        """Find files older than max_age_days in the given directory."""
        results: list[dict] = []
        if not os.path.isdir(path):
            return results
        now = time.time()
        cutoff = now - (max_age_days * 86400)
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        if stat.st_mtime < cutoff:
                            if entry.is_dir(follow_symlinks=False):
                                size = self._dir_size(entry.path)
                            else:
                                size = stat.st_size
                            size_mb = size / (1024 * 1024)
                            age_days = int((now - stat.st_mtime) / 86400)
                            results.append(
                                {
                                    "path": entry.path,
                                    "size_mb": round(size_mb, 1),
                                    "age_days": age_days,
                                }
                            )
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass
        return sorted(results, key=lambda x: x["size_mb"], reverse=True)

    def _dir_size(self, path: str) -> int:
        """Calculate total size of a directory recursively."""
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        if not os.path.islink(fp):
                            total += os.path.getsize(fp)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass
        return total

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
