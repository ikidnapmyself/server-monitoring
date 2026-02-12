"""Cross-platform disk analysis checker."""

import os
import sys
import time

from apps.checkers.checkers.base import BaseChecker, CheckResult


class DiskCommonChecker(BaseChecker):
    """Analyze disk usage on any Unix platform."""

    name = "disk_common"
    warning_threshold = 5000.0
    critical_threshold = 20000.0

    def check(self) -> CheckResult:
        try:
            scan_targets = ["/var/log", "~/.cache"]
            old_file_targets = ["/tmp", "/var/tmp"]
            large_file_targets = ["~"]

            space_hogs = []
            seen = set()
            for target in scan_targets:
                path = os.path.expanduser(target)
                for item in self._scan_directory(path):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        space_hogs.append(item)

            old_files = []
            for target in old_file_targets:
                path = os.path.expanduser(target)
                for item in self._find_old_files(path):
                    if item["path"] not in seen:
                        seen.add(item["path"])
                        old_files.append(item)

            large_files = []
            for target in large_file_targets:
                path = os.path.expanduser(target)
                for item in self._find_large_files(path):
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

    def _find_large_files(self, path: str, min_size_mb: float = 100.0) -> list[dict]:
        """Find files larger than min_size_mb in the given directory tree."""
        results: list[dict] = []
        if not os.path.isdir(path):
            return results
        min_size_bytes = min_size_mb * 1024 * 1024
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        if os.path.islink(fp):
                            continue
                        size = os.path.getsize(fp)
                        if size >= min_size_bytes:
                            results.append(
                                {
                                    "path": fp,
                                    "size_mb": round(size / (1024 * 1024), 1),
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
