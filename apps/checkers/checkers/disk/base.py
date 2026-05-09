"""Shared base class for disk-cleanup analysis checkers."""

import os
import sys
from abc import abstractmethod

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.disk.utils import (
    find_large_files,
    find_old_files,
    scan_directory,
)


class BaseDiskAnalyzer(BaseChecker):
    """Abstract base for disk cleanup analyzers.

    Subclasses are pure declarations of:
      - what to scan (three target lists + max_age_days)
      - when to run (platform predicate)
      - what advice to give (recommendation rules + section advice strings)

    All scanning, deduplication, sorting, totalling, and result-building
    is done here, identically for every subclass.
    """

    warning_threshold = 5000.0
    critical_threshold = 20000.0

    scan_targets: list[str] = []
    old_file_targets: list[str] = []
    large_file_targets: list[str] = []
    old_max_age_days: int = 7
    recommendation_rules: list[tuple[list[str], list[str]]] = []
    old_files_advice: str = ""
    large_files_advice: str = ""

    @abstractmethod
    def _is_applicable(self) -> bool:
        """True if this checker should run on the current platform."""
        ...  # pragma: no cover

    def check(self) -> CheckResult:
        if not self._is_applicable():
            return self._make_result(
                status=CheckStatus.OK,
                message="Skipped: not applicable for this platform",
                metrics={"platform": sys.platform},
            )
        try:
            seen: set[str] = set()
            space_hogs = self._collect(self.scan_targets, scan_directory, seen)
            old_files = self._collect(
                self.old_file_targets,
                find_old_files,
                seen,
                max_age_days=self.old_max_age_days,
            )
            exclude = {os.path.normpath(os.path.expanduser(t)) for t in self.scan_targets}
            large_files = self._collect(
                self.large_file_targets,
                find_large_files,
                seen,
                exclude_paths=exclude,
            )
            total = (
                sum(h["size_mb"] for h in space_hogs)
                + sum(f["size_mb"] for f in old_files)
                + sum(f["size_mb"] for f in large_files)
            )
            return self._make_result(
                status=self._determine_status(total),
                message=f"Disk analysis: {total:.1f} MB recoverable",
                metrics={
                    "platform": sys.platform,
                    "space_hogs": space_hogs,
                    "old_files": old_files,
                    "large_files": large_files,
                    "total_recoverable_mb": total,
                    "recommendations": self._build_recommendations(
                        space_hogs, old_files, large_files
                    ),
                },
            )
        except Exception as e:
            return self._error_result(str(e))

    def _collect(self, targets, scanner, seen, **scanner_kwargs):
        results: list[dict] = []
        for target in targets:
            path = os.path.expanduser(target)
            for item in scanner(path, timeout=self.timeout, **scanner_kwargs):
                if item["path"] not in seen:
                    seen.add(item["path"])
                    results.append(item)
        results.sort(key=lambda x: x["size_mb"], reverse=True)
        return results

    def _build_recommendations(self, space_hogs, old_files, large_files) -> list[list[str]]:
        recs: list[list[str]] = []
        paths = [h["path"] for h in space_hogs]
        for keywords, lines in self.recommendation_rules:
            if not lines:
                continue
            if any(kw in p for kw in keywords for p in paths):
                recs.append(list(lines))
        if old_files and self.old_files_advice:
            recs.append([self.old_files_advice])
        if large_files and self.large_files_advice:
            recs.append([self.large_files_advice])
        return recs
