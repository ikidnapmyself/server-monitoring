---
title: "Disk Checker Consolidation Design"
parent: Plans
---

# Disk Checker Consolidation Design

## Problem

The disk-related code in `apps/checkers/checkers/` is spread across five top-level files:

- `disk.py` — `DiskChecker` (utilization: `psutil.disk_usage`, %-full alerting)
- `disk_common.py` — `DiskCommonChecker` (cleanup analysis, cross-platform Unix)
- `disk_macos.py` — `DiskMacOSChecker` (cleanup analysis, macOS-specific paths)
- `disk_linux.py` — `DiskLinuxChecker` (cleanup analysis, Linux-specific paths)
- `disk_utils.py` — `scan_directory` / `find_old_files` / `find_large_files` / `dir_size`

The three cleanup checkers are ~80% copy-paste of each other. Each one independently implements: skip-if-platform-mismatch → for each target list, collect-dedup-sort → compute total → build recommendations → return result. The genuine differences are pure configuration: platform predicate, three target lists, `max_age_days`, and the recommendation rules. Changes to the shared algorithm (such as PR #135's "globally sort across scan targets") had to land in three places.

Two additional consequences of the duplication:

- `disk_macos` and `disk_linux` lack `large_file_targets` entirely. The capability exists in `disk_common` only because it happens to have been written first; macOS and Linux were never aligned with it.
- The five-file flat layout makes it harder to reason about what's a utility, what's a checker, and which checkers share behavior.

## Scope

In scope:
- Move the five disk files into a new `apps/checkers/checkers/disk/` subpackage.
- Extract the shared cleanup-analysis algorithm into `BaseDiskAnalyzer` in `disk/base.py`.
- Rewrite the three cleanup subclasses (`disk/common.py`, `disk/macos.py`, `disk/linux.py`) as ~25-line declarative configurations of `BaseDiskAnalyzer`.
- Add concrete `large_file_targets` for macOS (`/Library/Application Support`, `/Users/Shared`) and Linux (`/srv`, `/opt`) — paths outside `~` so they don't double-walk anything `disk_common`'s `~` already covers.
- Add `_tests/checkers/disk/test_base.py` testing `BaseDiskAnalyzer.check()` directly via a tiny test-only subclass.
- Update all existing test files: move into `_tests/checkers/disk/`, update `@patch` decorator paths to point at `disk.base.scan_directory` etc. (the helpers are now imported in `disk/base.py`, not in the subclass modules).
- Update `apps/checkers/checkers/__init__.py` to import from the new paths. Re-export from `disk/__init__.py` so `from apps.checkers.checkers.disk import DiskCommonChecker` works.

Out of scope:
- Changing `DiskChecker` (utilization). Code body unchanged; only the file moves to `disk/usage.py`.
- Touching `CHECKER_REGISTRY` keys (`disk`, `disk_common`, `disk_macos`, `disk_linux` stay the same). Registry callers see no change.
- Threshold tuning (`5000` / `20000` MB stay).
- Output-format changes in `check_health` / `run_check`.
- Merging the three cleanup checkers into one. They remain three classes for the same reason as today: each can be enabled/disabled independently per environment, and `disk_common` runs alongside the platform-specific one (complementary, not redundant).

## Approach — Approach 1 (class-attribute config + abstract method for platform predicate)

The base class follows the same idiom as `BaseChecker` itself: static configuration via class attributes (`name`, `warning_threshold`, target lists, `recommendation_rules`); per-class behavior via a small number of methods. Subclasses are pure declarations.

### `apps/checkers/checkers/disk/base.py`

```python
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

    warning_threshold = 5000.0       # MB recoverable
    critical_threshold = 20000.0     # MB recoverable

    # Subclasses override these
    scan_targets: list[str] = []
    old_file_targets: list[str] = []
    large_file_targets: list[str] = []
    old_max_age_days: int = 7
    recommendation_rules: list[tuple[list[str], str]] = []
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

    def _build_recommendations(self, space_hogs, old_files, large_files) -> list[str]:
        recs: list[str] = []
        paths = [h["path"] for h in space_hogs]
        for keywords, advice in self.recommendation_rules:
            if any(kw in p for kw in keywords for p in paths):
                recs.append(advice)
        if old_files and self.old_files_advice:
            recs.append(self.old_files_advice)
        if large_files and self.large_files_advice:
            recs.append(self.large_files_advice)
        return recs
```

### `apps/checkers/checkers/disk/common.py`

```python
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
```

### `apps/checkers/checkers/disk/macos.py`

```python
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
```

### `apps/checkers/checkers/disk/linux.py`

```python
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
        (["apt"], "Run 'sudo apt clean' to clear APT package cache"),
        (["journal"], "Run 'sudo journalctl --vacuum-size=100M' to trim journal logs"),
        (["docker"], "Run 'docker system prune' to clean unused Docker data"),
        (["snap"], "Remove old snap package revisions"),
    ]
    old_files_advice = "Remove old temporary files from /tmp"
    large_files_advice = "Review and remove large files in /srv and /opt"

    def _is_applicable(self) -> bool:
        return sys.platform == "linux"
```

### `apps/checkers/checkers/disk/__init__.py`

Re-exports for convenience and backward-compat:

```python
from apps.checkers.checkers.disk.common import DiskCommonChecker
from apps.checkers.checkers.disk.linux import DiskLinuxChecker
from apps.checkers.checkers.disk.macos import DiskMacOSChecker
from apps.checkers.checkers.disk.usage import DiskChecker

__all__ = [
    "DiskChecker",
    "DiskCommonChecker",
    "DiskLinuxChecker",
    "DiskMacOSChecker",
]
```

### File moves with no body changes

- `disk.py` → `disk/usage.py` — `DiskChecker` (utilization) class body byte-identical.
- `disk_utils.py` → `disk/utils.py` — helper functions byte-identical.

## File layout

```
apps/checkers/checkers/disk/
    __init__.py        # re-exports the 4 checker classes
    usage.py           # DiskChecker (utilization, byte-identical move)
    base.py            # BaseDiskAnalyzer (abstract: shared check() implementation)
    common.py          # DiskCommonChecker  (~25 lines)
    macos.py           # DiskMacOSChecker   (~25 lines, includes new large_file_targets)
    linux.py           # DiskLinuxChecker   (~25 lines, includes new large_file_targets)
    utils.py           # scan_directory, find_old_files, find_large_files, dir_size

apps/checkers/_tests/checkers/disk/
    __init__.py
    test_usage.py      # was test_disk.py
    test_base.py       # NEW: tests BaseDiskAnalyzer.check() via a stub subclass
    test_common.py     # was test_disk_common.py
    test_macos.py      # was test_disk_macos.py
    test_linux.py      # was test_disk_linux.py
    test_utils.py      # was test_disk_utils.py
```

`apps/checkers/checkers/__init__.py` updates the four imports from `disk_common` / `disk_macos` / `disk_linux` / `disk` to `disk.common` / `disk.macos` / `disk.linux` / `disk.usage`. `CHECKER_REGISTRY` keys are unchanged.

## Test patch paths

The existing tests use `@patch("apps.checkers.checkers.disk_common.scan_directory", ...)` because each subclass module currently imports the helpers directly. After consolidation, the helpers are imported only in `disk/base.py`, so all `@patch` paths must move:

| Before | After |
|---|---|
| `apps.checkers.checkers.disk_common.scan_directory` | `apps.checkers.checkers.disk.base.scan_directory` |
| `apps.checkers.checkers.disk_common.find_old_files` | `apps.checkers.checkers.disk.base.find_old_files` |
| `apps.checkers.checkers.disk_common.find_large_files` | `apps.checkers.checkers.disk.base.find_large_files` |
| Same applies to `disk_macos.*` and `disk_linux.*` |

`os.path.expanduser` and `sys` patches in the existing tests continue to point at the subclass modules where they're imported (`disk.macos`, `disk.linux`, etc.). `disk.common` no longer imports `os` directly (it does, for the `os.name == "posix"` predicate); patches on `os` should target `disk.common` if asserting platform behavior on `disk_common`.

Mechanically: most `@patch` decorators in `test_disk_common.py` / `test_disk_macos.py` / `test_disk_linux.py` get one path-segment update.

## New test: `test_base.py`

Direct unit tests for the algorithm, using a tiny stub subclass:

```python
class _StubAnalyzer(BaseDiskAnalyzer):
    name = "_stub"
    scan_targets = ["/test/scan"]
    old_file_targets = ["/test/old"]
    large_file_targets = ["/test/large"]
    old_max_age_days = 7
    recommendation_rules = [(["match"], "matched advice")]
    old_files_advice = "old advice"
    large_files_advice = "large advice"

    def _is_applicable(self) -> bool:
        return True
```

Tests cover:
- `_is_applicable() returns False` → returns OK with "Skipped" message and `platform` metric only.
- All three lists populated → metrics dict has all four keys + sorted lists + correct `total_recoverable_mb`.
- Empty `scan_targets`, full others → `space_hogs == []`, others populated, total reflects only the populated lists.
- `recommendation_rules` matching → advice appears when a path contains the keyword; absent otherwise.
- `old_files_advice` / `large_files_advice` → present iff respective list is non-empty.
- Exception in scanner → returns UNKNOWN with error message via `_error_result`.

Per-subclass test files (`test_common.py`, `test_macos.py`, `test_linux.py`) keep their existing platform-applicability tests, scan-target-coverage tests, and recommendation tests, with patch paths updated. Multi-target sort tests from PR #135 stay relevant — the algorithm in the base class still sorts each list across targets.

## Edge cases

- **`disk.py` (utilization) move.** Code body unchanged; just `disk.py` → `disk/usage.py`. Tests update import path only.
- **`__init__.py` surface preservation.** External callers that import `DiskChecker` / `DiskCommonChecker` / etc. from `apps.checkers.checkers` see no change. Direct imports from old paths (`from apps.checkers.checkers.disk_common import ...`) break — verified that no such direct imports exist outside `apps/checkers/_tests/`.
- **Empty target lists.** `_collect()` returns `[]`; the corresponding `metrics` key is an empty list; advice for that section isn't appended. Acceptable — same shape as today's metrics dict for sections that don't apply.
- **Empty `recommendation_rules`.** Loop runs zero times; only the `old_files_advice` / `large_files_advice` are conditionally appended. Acceptable.
- **Subclass missing `_is_applicable`.** `ABC` raises `TypeError: Can't instantiate abstract class` at registry import. Loud failure at startup.
- **`BaseDiskAnalyzer` itself never instantiated.** Abstract; only concrete subclasses appear in `CHECKER_REGISTRY`.
- **`recommendation_rules` keyword matching is case-sensitive substring.** Matches today's behavior. Future case-insensitivity is a separate decision.
- **Threshold values stay at 5000/20000 MB on the base.** Subclasses don't override.
- **`exclude_paths` for `find_large_files`** is computed from `scan_targets` (same as `disk_common` does today). On `disk_macos` and `disk_linux`, this means the four-Library-or-four-/var-paths get excluded from the large-file walk — fine, since their `large_file_targets` (`/Library/Application Support`, `/Users/Shared`, `/srv`, `/opt`) don't overlap with `scan_targets` anyway.
- **Test discovery.** New `_tests/checkers/disk/__init__.py` lets pytest find the moved tests. Existing `_tests/checkers/__init__.py` stays.

## Notes for implementation

- **Bisect-friendly commit split.** Suggested two commits:
  1. **Move** `disk.py` and `disk_utils.py` to `disk/usage.py` and `disk/utils.py`; create `disk/__init__.py`; update imports in `apps/checkers/checkers/__init__.py` and tests. No behavior change.
  2. **Refactor + extend** `disk_common`/`disk_macos`/`disk_linux` to use `BaseDiskAnalyzer`. Adds `disk/base.py`, rewrites the three subclass files, adds `test_base.py`, updates patch paths, adds new `large_file_targets` for macOS/Linux.
- **No changes to `CHECKER_REGISTRY` keys.** Operators see no migration.
- **No new `--flag` arguments.** No CLI change.
- **No metrics-shape change**, except that `disk_macos` and `disk_linux` now ship a non-empty `large_files` list when their scans find anything > 100 MB at the new targets. Consumers (CLI formatter, JSON path, intelligence layer) already handle a non-empty `large_files` correctly thanks to the helper added in #136.
- **Performance.** macOS gets two new walks: `/Library/Application Support` and `/Users/Shared`. Both should be small enough to walk quickly (Application Support is per-user-app, Shared is typically empty or a few VM disks). Linux gets `/srv` and `/opt` — `/srv` is often empty, `/opt` typically a handful of installed packages. The `find_large_files` 100 MB floor + `timeout=self.timeout` keep these bounded even on heavily-populated boxes.