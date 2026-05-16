---
title: "2026-05-09 Disk Checker Consolidation Implementation Plan"
parent: Plans
---

# Disk Checker Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate the five disk-related files into an `apps/checkers/checkers/disk/` subpackage and extract the duplicated cleanup-analysis logic into a shared `BaseDiskAnalyzer` so the three cleanup checkers (`disk_common`, `disk_macos`, `disk_linux`) become ~25-line declarative configurations.

**Architecture:** Two-commit split. **Commit A** is a pure file move: `disk.py` → `disk/usage.py`, `disk_utils.py` → `disk/utils.py`, plus the matching test files. No class changes. **Commit B** introduces `disk/base.py:BaseDiskAnalyzer` as a class-attribute-config + abstract-`_is_applicable` template; rewrites `disk_common.py` / `disk_macos.py` / `disk_linux.py` as `disk/common.py` / `disk/macos.py` / `disk/linux.py` with class attributes only; adds concrete `large_file_targets` for macOS (`/Library/Application Support`, `/Users/Shared`) and Linux (`/srv`, `/opt`); migrates test patch paths to point at `disk.base.*`.

**Tech Stack:** Python 3, Django management commands, `unittest.TestCase` via `django.test.TestCase`, `pytest`, `coverage`.

**Design doc:** `docs/plans/2026-05-09-disk-checker-consolidation-design.md`

**Branch:** `fix/disk-checker-consolidation` (already created from `main`, design doc committed at `5734526`).

**Single PR with two commits.** Bisect-friendly: file moves vs. behavior change in different commits.

---

## Background — what's changing

Five top-level files become a 7-file subpackage:

```
apps/checkers/checkers/
  disk.py                   →   disk/usage.py     (byte-identical move)
  disk_utils.py             →   disk/utils.py     (byte-identical move)
  disk_common.py            →   disk/common.py    (rewritten as ~25-line declaration)
  disk_macos.py             →   disk/macos.py     (rewritten + new large_file_targets)
  disk_linux.py             →   disk/linux.py     (rewritten + new large_file_targets)
                            +   disk/base.py      (NEW: BaseDiskAnalyzer)
                            +   disk/__init__.py  (NEW: re-exports the 4 classes)
```

Tests mirror the change:

```
apps/checkers/_tests/checkers/
  test_disk.py              →   disk/test_usage.py
  test_disk_utils.py        →   disk/test_utils.py
  test_disk_common.py       →   disk/test_common.py
  test_disk_macos.py        →   disk/test_macos.py
  test_disk_linux.py        →   disk/test_linux.py
                            +   disk/test_base.py     (NEW: BaseDiskAnalyzer unit tests)
                            +   disk/__init__.py      (NEW)
```

`CHECKER_REGISTRY` keys (`disk`, `disk_common`, `disk_macos`, `disk_linux`) are unchanged. Output metrics shape is unchanged. Operators see no migration.

**Patch path migration in the cleanup tests.** Today's tests use `@patch("apps.checkers.checkers.disk_common.scan_directory", ...)` because each subclass module imports the helpers at module level. After Commit B, the helpers are imported only in `disk/base.py`, so all patch paths move to `apps.checkers.checkers.disk.base.scan_directory` (etc.). 106 `@patch` decorators across the three cleanup test files need this update.

---

## Commit A — File reorganization (no behavior change)

### Task 1: Set up the `disk/` subpackage and move byte-identical files

This commit only moves files and updates imports. The three cleanup checker classes stay where they are at the top level for now — only their `from disk_utils` imports change to `from disk.utils`.

**Files to create:**
- `apps/checkers/checkers/disk/__init__.py` (initial — re-exports only `DiskChecker`)
- `apps/checkers/_tests/checkers/disk/__init__.py` (empty file for test discovery)

**Files to move (no body change):**
- `apps/checkers/checkers/disk.py` → `apps/checkers/checkers/disk/usage.py`
- `apps/checkers/checkers/disk_utils.py` → `apps/checkers/checkers/disk/utils.py`
- `apps/checkers/_tests/checkers/test_disk.py` → `apps/checkers/_tests/checkers/disk/test_usage.py`
- `apps/checkers/_tests/checkers/test_disk_utils.py` → `apps/checkers/_tests/checkers/disk/test_utils.py`

**Files to modify (one-line import update each):**
- `apps/checkers/checkers/disk_common.py` line 7: `from apps.checkers.checkers.disk_utils import (...)` → `from apps.checkers.checkers.disk.utils import (...)`
- `apps/checkers/checkers/disk_macos.py` line 7: same change
- `apps/checkers/checkers/disk_linux.py` line 6: same change
- `apps/checkers/checkers/__init__.py` line 4: `from apps.checkers.checkers.disk import DiskChecker` → `from apps.checkers.checkers.disk.usage import DiskChecker`
- `apps/checkers/_tests/checkers/disk/test_usage.py`: change `from apps.checkers.checkers.disk import DiskChecker` to `from apps.checkers.checkers.disk.usage import DiskChecker` (only at the local imports inside `_get_checker_class()` — the test file uses lazy imports inside the test methods)
- `apps/checkers/_tests/checkers/disk/test_utils.py` line 7: `from apps.checkers.checkers.disk_utils import (...)` → `from apps.checkers.checkers.disk.utils import (...)`

**Step 1: Create the directories and move files**

```bash
mkdir -p apps/checkers/checkers/disk apps/checkers/_tests/checkers/disk
git mv apps/checkers/checkers/disk.py apps/checkers/checkers/disk/usage.py
git mv apps/checkers/checkers/disk_utils.py apps/checkers/checkers/disk/utils.py
git mv apps/checkers/_tests/checkers/test_disk.py apps/checkers/_tests/checkers/disk/test_usage.py
git mv apps/checkers/_tests/checkers/test_disk_utils.py apps/checkers/_tests/checkers/disk/test_utils.py
```

**Step 2: Create `apps/checkers/checkers/disk/__init__.py`**

```python
"""Disk-related checkers."""

from apps.checkers.checkers.disk.usage import DiskChecker

__all__ = ["DiskChecker"]
```

(The cleanup classes will be added to `__all__` in Commit B.)

**Step 3: Create `apps/checkers/_tests/checkers/disk/__init__.py` as an empty file**

Empty content. Just enables pytest discovery.

**Step 4: Update the four import sites**

Edit each of the following with a one-line change:

- `apps/checkers/checkers/disk_common.py` — change line 7 import.
- `apps/checkers/checkers/disk_macos.py` — change line 7 import.
- `apps/checkers/checkers/disk_linux.py` — change line 6 import.
- `apps/checkers/checkers/__init__.py` — change line 4 import.
- `apps/checkers/_tests/checkers/disk/test_usage.py` — change the `from apps.checkers.checkers.disk import DiskChecker` line(s) to `from apps.checkers.checkers.disk.usage import DiskChecker`. Note: in the existing `test_disk.py`, the import is inside `_get_checker_class()`. Adjust accordingly.
- `apps/checkers/_tests/checkers/disk/test_utils.py` — change line 7 import.

**Step 5: Run the full suite**

Run: `uv run pytest apps/checkers/ -v 2>&1 | tail -10`

Expected: every test passes. The three cleanup checker tests still pass because `disk_common.py` / `disk_macos.py` / `disk_linux.py` still exist at top level and now import their helpers via `disk.utils`. The `@patch("apps.checkers.checkers.disk_common.scan_directory")` decorators still work because `scan_directory` is bound in the `disk_common` module namespace through the import.

If any test fails, investigate before continuing. Common cause: missed updating an import.

**Step 6: Lint, format, type-check**

```bash
uv run black --check apps/checkers/
uv run ruff check apps/checkers/
uv run mypy apps/checkers/checkers/
```

Expected: clean.

**Step 7: Commit (Commit A — file reorganization)**

```bash
git add apps/checkers/
git commit -m "$(cat <<'EOF'
refactor(checkers): move disk files into disk/ subpackage

Pure file move. disk.py becomes disk/usage.py, disk_utils.py becomes
disk/utils.py, and the matching test files move to _tests/checkers/disk/.
The three cleanup checkers (disk_common.py, disk_macos.py, disk_linux.py)
stay at the top level for this commit; only their import of the helpers
shifts from disk_utils to disk.utils. CHECKER_REGISTRY keys unchanged.
No behavior change.

A follow-up commit will move and refactor the cleanup checkers onto a
shared BaseDiskAnalyzer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, `git status` clean.

---

## Commit B — Extract `BaseDiskAnalyzer` and consolidate cleanup checkers

### Task 2: Create the base class

**Files to create:**
- `apps/checkers/checkers/disk/base.py`

**Step 1: Write `disk/base.py`**

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

    warning_threshold = 5000.0
    critical_threshold = 20000.0

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

This is the algorithm verbatim from the three cleanup checkers, factored once.

---

### Task 3: Add direct unit tests for `BaseDiskAnalyzer`

**Files to create:**
- `apps/checkers/_tests/checkers/disk/test_base.py`

**Step 1: Write the tests**

```python
"""Direct unit tests for BaseDiskAnalyzer using a stub subclass."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk.base import BaseDiskAnalyzer


class _StubAnalyzer(BaseDiskAnalyzer):
    """Test-only concrete subclass with deterministic config."""

    name = "_stub"
    scan_targets = ["/test/scan"]
    old_file_targets = ["/test/old"]
    large_file_targets = ["/test/large"]
    old_max_age_days = 7
    recommendation_rules = [(["match_keyword"], "matched advice")]
    old_files_advice = "old advice"
    large_files_advice = "large advice"

    def _is_applicable(self) -> bool:
        return True


class _NonApplicableAnalyzer(_StubAnalyzer):
    name = "_nonapplicable"

    def _is_applicable(self) -> bool:
        return False


class BaseDiskAnalyzerTests(TestCase):
    """Direct tests of BaseDiskAnalyzer.check()."""

    def test_skips_when_not_applicable(self):
        result = _NonApplicableAnalyzer().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("Skipped", result.message)
        self.assertIn("platform", result.metrics)
        self.assertNotIn("space_hogs", result.metrics)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_collects_all_three_lists(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = [{"path": "/test/scan/a", "size_mb": 100.0}]
        mock_old.return_value = [
            {"path": "/test/old/b", "size_mb": 50.0, "age_days": 10}
        ]
        mock_large.return_value = [{"path": "/test/large/c", "size_mb": 200.0}]

        result = _StubAnalyzer().check()

        self.assertEqual(len(result.metrics["space_hogs"]), 1)
        self.assertEqual(len(result.metrics["old_files"]), 1)
        self.assertEqual(len(result.metrics["large_files"]), 1)
        self.assertAlmostEqual(result.metrics["total_recoverable_mb"], 350.0)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_dedup_across_lists(self, mock_scan, mock_old, mock_large):
        # Same path appears in scan and old; should only be counted once.
        mock_scan.return_value = [{"path": "/dup", "size_mb": 100.0}]
        mock_old.return_value = [{"path": "/dup", "size_mb": 100.0, "age_days": 5}]
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertEqual(len(result.metrics["space_hogs"]), 1)
        self.assertEqual(len(result.metrics["old_files"]), 0)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_recommendation_rule_matches_keyword(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = [
            {"path": "/test/scan/match_keyword/x", "size_mb": 10.0}
        ]
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertIn("matched advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_recommendation_rule_does_not_match(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = [{"path": "/test/scan/other/x", "size_mb": 10.0}]
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertNotIn("matched advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_old_files_advice_appears_when_old_files_present(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = []
        mock_old.return_value = [
            {"path": "/test/old/x", "size_mb": 10.0, "age_days": 30}
        ]
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertIn("old advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_large_files_advice_appears_when_large_files_present(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = [{"path": "/test/large/x", "size_mb": 200.0}]

        result = _StubAnalyzer().check()

        self.assertIn("large advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_advice_omitted_when_section_empty(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertNotIn("old advice", result.metrics["recommendations"])
        self.assertNotIn("large advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_scanner_exception_returns_unknown(self, mock_scan):
        mock_scan.side_effect = OSError("boom")

        result = _StubAnalyzer().check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("boom", result.message)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_lists_globally_sorted_desc(self, mock_scan, mock_old, mock_large):
        # Multi-target case: returns small then large; check() must sort.
        def fake_scan(path, timeout=None):
            if path == "/test/scan":
                return [
                    {"path": "/a", "size_mb": 5.0},
                    {"path": "/b", "size_mb": 100.0},
                ]
            return []

        mock_scan.side_effect = fake_scan
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()
        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class _MultiTargetStub(BaseDiskAnalyzer):
    """Stub with multiple scan targets to exercise cross-target sort."""

    name = "_multi"
    scan_targets = ["/first", "/second"]
    old_file_targets = []
    large_file_targets = []
    old_max_age_days = 7

    def _is_applicable(self) -> bool:
        return True


class MultiTargetSortTests(TestCase):
    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_globally_sorts_across_multiple_targets(
        self, mock_scan, _old, _large
    ):
        def fake_scan(path, timeout=None):
            if path == "/first":
                return [{"path": "/first/small", "size_mb": 5.0}]
            if path == "/second":
                return [{"path": "/second/big", "size_mb": 500.0}]
            return []

        mock_scan.side_effect = fake_scan

        result = _MultiTargetStub().check()
        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 500.0)
```

**Step 2: Run the new tests against the new base**

Run: `uv run pytest apps/checkers/_tests/checkers/disk/test_base.py -v`

Expected: all 11 PASS. The base class is fully implemented; these tests exercise the algorithm directly.

If any fails, the base class has a bug — investigate before touching the subclasses.

---

### Task 4: Rewrite `disk_common.py` as `disk/common.py`

**Files to delete (after move):**
- `apps/checkers/checkers/disk_common.py`

**Files to create:**
- `apps/checkers/checkers/disk/common.py`

**Step 1: Use `git mv` to preserve history attribution where possible**

```bash
git mv apps/checkers/checkers/disk_common.py apps/checkers/checkers/disk/common.py
```

**Step 2: Replace the entire file body with the declarative subclass**

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

The original file's body (which had its own `check()`, helper imports, `_build_recommendations`, etc.) is replaced wholesale.

---

### Task 5: Rewrite `disk_macos.py` as `disk/macos.py`

```bash
git mv apps/checkers/checkers/disk_macos.py apps/checkers/checkers/disk/macos.py
```

Replace the entire file body:

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

Note the **new** `large_file_targets = ["/Library/Application Support", "/Users/Shared"]` — an additive functional change vs. the pre-PR macOS checker, which had no large-file scanning at all.

---

### Task 6: Rewrite `disk_linux.py` as `disk/linux.py`

```bash
git mv apps/checkers/checkers/disk_linux.py apps/checkers/checkers/disk/linux.py
```

Replace the entire file body:

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

New `large_file_targets = ["/srv", "/opt"]` — same additive change pattern.

---

### Task 7: Update `disk/__init__.py` and `apps/checkers/checkers/__init__.py`

**Step 1: Update `apps/checkers/checkers/disk/__init__.py`**

Replace the placeholder content (which only re-exported `DiskChecker` from Commit A) with the full re-export:

```python
"""Disk-related checkers."""

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

**Step 2: Update `apps/checkers/checkers/__init__.py`**

Change three import lines:

- `from apps.checkers.checkers.disk_common import DiskCommonChecker` → `from apps.checkers.checkers.disk.common import DiskCommonChecker`
- `from apps.checkers.checkers.disk_linux import DiskLinuxChecker` → `from apps.checkers.checkers.disk.linux import DiskLinuxChecker`
- `from apps.checkers.checkers.disk_macos import DiskMacOSChecker` → `from apps.checkers.checkers.disk.macos import DiskMacOSChecker`

`CHECKER_REGISTRY` keys are unchanged. `__all__` order may need a re-sort to stay alphabetical; black/ruff will tell you.

---

### Task 8: Move and update the cleanup test files

```bash
git mv apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/disk/test_common.py
git mv apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/disk/test_macos.py
git mv apps/checkers/_tests/checkers/test_disk_linux.py apps/checkers/_tests/checkers/disk/test_linux.py
```

**Step 1: Update the `_get_checker_class` import paths in each moved test file**

In each moved file, find lines like:

```python
def _get_checker_class(self):
    from apps.checkers.checkers.disk_common import DiskCommonChecker
    return DiskCommonChecker
```

Update to:

```python
def _get_checker_class(self):
    from apps.checkers.checkers.disk.common import DiskCommonChecker
    return DiskCommonChecker
```

(And similarly for `disk_macos` → `disk.macos` and `disk_linux` → `disk.linux`.) Each test file has multiple of these scattered through; use grep to find them all.

**Step 2: Migrate the `@patch` decorators**

This is the bulk of the test-file work. The cleanup checker tests today use patch paths like:

```python
@patch("apps.checkers.checkers.disk_common.scan_directory")
@patch("apps.checkers.checkers.disk_common.find_old_files")
@patch("apps.checkers.checkers.disk_common.find_large_files")
@patch("apps.checkers.checkers.disk_common.os.path.expanduser")
```

These need to migrate to:

```python
@patch("apps.checkers.checkers.disk.base.scan_directory")
@patch("apps.checkers.checkers.disk.base.find_old_files")
@patch("apps.checkers.checkers.disk.base.find_large_files")
@patch("apps.checkers.checkers.disk.base.os.path.expanduser")  # OR disk.common.os.path.expanduser, see below
```

**Why `disk.base`:** the helpers (`scan_directory`, `find_old_files`, `find_large_files`) and `os` are now imported only in `disk/base.py`. The subclass modules (`disk/common.py`, `disk/macos.py`, `disk/linux.py`) don't import these directly anymore.

**`os.path.expanduser` patch path** — this is a bit subtle. The base class calls `os.path.expanduser(target)` inside `_collect`. The `os` symbol bound in `disk.base` is what gets used. So patches go to `disk.base.os.path.expanduser`.

`sys` patches in `test_macos.py` / `test_linux.py` (`@patch("apps.checkers.checkers.disk_macos.sys")`) likewise migrate to `apps.checkers.checkers.disk.base.sys` because `sys` is now imported in `disk/base.py` (used by the skip path) and not in the subclass modules.

Wait — `sys` is used inside `_is_applicable()` on the subclasses. So the subclass modules DO need `import sys`. Let me re-check:

- `disk/macos.py`: `import sys` (used in `_is_applicable`)
- `disk/linux.py`: `import sys` (used in `_is_applicable`)
- `disk/common.py`: `import os` (used in `_is_applicable`)
- `disk/base.py`: `import sys` (used inside `check()` for the skip-message metrics)

So a `@patch("apps.checkers.checkers.disk.macos.sys")` patches the `sys` binding inside `disk.macos`. That's where `_is_applicable()` reads `sys.platform`. So tests that mock `sys.platform = "darwin"` to make the macOS checker run should patch `disk.macos.sys`.

Similarly, `@patch("apps.checkers.checkers.disk.linux.sys")` for the Linux subclass.

But the `sys.platform` value that ends up in the result metrics dict (`"platform": sys.platform`) is read inside `disk.base.check()` — i.e., `disk.base.sys.platform`. So if a test asserts on `result.metrics["platform"]`, it might need to patch BOTH `disk.macos.sys` (for `_is_applicable`) and `disk.base.sys` (for the metrics read).

Pragmatic approach: most existing tests don't assert on `result.metrics["platform"]` — they only check status / message / list shapes. So patching `disk.macos.sys` to make `_is_applicable` return True is enough. If a test fails because it asserts on the platform metric, patch `disk.base.sys` too.

**`os.path.expanduser` patch path** — `_collect()` in `disk.base` uses `os.path.expanduser`. The `os` binding lives in `disk.base.os`. So patches go to `disk.base.os.path.expanduser`.

Mechanically, do this with sed-style replacement, then verify by running:

```bash
# Inside each of the three moved test files:
# Replace the substring "apps.checkers.checkers.disk_common." → "apps.checkers.checkers.disk.base."
#   for scan_directory, find_old_files, find_large_files, os.path.expanduser
# Replace "apps.checkers.checkers.disk_common.os" → "apps.checkers.checkers.disk.base.os"
# Same for disk_macos and disk_linux.
# But for sys.platform mocks (in disk_macos / disk_linux tests), keep as
#   "apps.checkers.checkers.disk.macos.sys" / "apps.checkers.checkers.disk.linux.sys"
#   (subclass _is_applicable reads sys from its own module).
```

Suggested mechanical procedure:
1. In `test_common.py`: replace `disk_common.scan_directory` → `disk.base.scan_directory`, `disk_common.find_old_files` → `disk.base.find_old_files`, `disk_common.find_large_files` → `disk.base.find_large_files`, `disk_common.os.path.expanduser` → `disk.base.os.path.expanduser`. Keep any `sys` patches pointing at `disk.common` if they exist (the existing test doesn't seem to have sys patches).
2. In `test_macos.py`: same scan/old/large/expanduser migrations to `disk.base`. KEEP `disk_macos.sys` → `disk.macos.sys` (it's the subclass's `sys` binding for `_is_applicable`).
3. In `test_linux.py`: same. KEEP `disk_linux.sys` → `disk.linux.sys`.

**Step 3: Run the cleanup tests**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/test_common.py apps/checkers/_tests/checkers/disk/test_macos.py apps/checkers/_tests/checkers/disk/test_linux.py -v 2>&1 | tail -20
```

Expected: all PASS.

If a test fails because of a missed patch path, fix it. Common failures:
- `AttributeError: module 'apps.checkers.checkers.disk.common' has no attribute 'scan_directory'` — patch path still points at the subclass module; update to `disk.base`.
- An assertion fails because metrics changed shape — should not happen if the base preserves byte-identical algorithm; investigate.
- A `large_files`-related test fails on macOS or Linux because the test was written assuming `large_files` was always empty — these tests need updating to either ignore `large_files` or to mock `find_large_files` returning `[]` explicitly.

**Step 4: Add tests for the new `large_file_targets` on macOS and Linux**

Append to `test_macos.py` (inside `DiskMacOSCheckerTests`):

```python
    @patch("apps.checkers.checkers.disk.macos.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_large_files")
    def test_walks_application_support_for_large_files(
        self, mock_large, _old, _scan, mock_sys
    ):
        """macOS large_file_targets includes /Library/Application Support."""
        mock_sys.platform = "darwin"
        mock_large.return_value = [
            {"path": "/Library/Application Support/foo/big.db", "size_mb": 500.0},
        ]
        result = self._get_checker_class()().check()
        self.assertGreater(len(result.metrics["large_files"]), 0)
        self.assertEqual(result.metrics["large_files"][0]["size_mb"], 500.0)
```

Append to `test_linux.py` (inside `DiskLinuxCheckerTests`):

```python
    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_large_files")
    def test_walks_srv_and_opt_for_large_files(
        self, mock_large, _old, _scan, mock_sys
    ):
        """Linux large_file_targets includes /srv and /opt."""
        mock_sys.platform = "linux"
        mock_large.return_value = [
            {"path": "/srv/data/big.bin", "size_mb": 2000.0},
            {"path": "/opt/app/lib.so", "size_mb": 150.0},
        ]
        result = self._get_checker_class()().check()
        sizes = [item["size_mb"] for item in result.metrics["large_files"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 2000.0)
```

**Step 5: Run the full disk-test directory**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/ -v
```

Expected: all PASS.

---

### Task 9: Verify, lint, commit

**Step 1: Run the full apps/checkers/ suite**

```bash
uv run pytest apps/checkers/ -v 2>&1 | tail -10
```

Expected: all PASS.

**Step 2: Coverage**

```bash
uv run coverage run --branch -m pytest apps/checkers/_tests/checkers/disk/
uv run coverage report -m --include='apps/checkers/checkers/disk/*'
```

Expected: 100% on every file in `disk/`.

**Step 3: Lint, format, type-check**

```bash
uv run black --check apps/checkers/
uv run ruff check apps/checkers/
uv run mypy apps/checkers/checkers/
```

Expected: clean.

**Step 4: Live sanity check (this Mac is darwin)**

```bash
uv run python manage.py check_health disk_common
uv run python manage.py check_health disk_macos
uv run python manage.py check_health disk
```

Verify:
- `disk_common`: still produces space_hogs/old_files/large_files; subtotals reconcile.
- `disk_macos`: now includes a `Large Files` section if anything in `/Library/Application Support` or `/Users/Shared` exceeds 100 MB. If both are empty/small, the section may be absent (empty list); that's OK.
- `disk`: utilization output is unchanged shape (`disks: {/: {percent, total_gb, used_gb, free_gb}}`).
- `run_check disk_common --json`: JSON output has the expected keys.

**Step 5: Commit (Commit B — refactor + extend)**

```bash
git add apps/checkers/
git commit -m "$(cat <<'EOF'
refactor(checkers): extract BaseDiskAnalyzer; align disk cleanup checkers

The three disk cleanup checkers (disk_common, disk_macos, disk_linux)
duplicated ~80% of their structure: skip-if-platform-mismatch, collect
each target list, dedup against a seen-set, sort, total, build
recommendations, return result. Extract that algorithm into
BaseDiskAnalyzer in disk/base.py. Each subclass becomes a ~25-line
declaration: target lists, max_age_days, platform predicate, and a
data-driven recommendation_rules table.

Adds concrete large_file_targets to macOS and Linux:
  - macOS: /Library/Application Support, /Users/Shared
  - Linux: /srv, /opt
Both outside ~ so they don't double-walk anything disk_common's ~ scan
already covers. metrics["large_files"] for these checkers is now
populated from real scans instead of being absent entirely.

CHECKER_REGISTRY keys unchanged. Output metrics shape unchanged.
test_base.py adds direct tests for BaseDiskAnalyzer via a stub
subclass, covering applicability skip, dedup, recommendation rules,
section advice, exception path, and multi-target sort. Cleanup test
patch paths migrate from disk_common/disk_macos/disk_linux to
disk.base where the helpers are now imported.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, `git status` clean. `git log --oneline main..HEAD` should show three commits (design doc + Commit A + Commit B).

---

## Task 10: Push and open PR

```bash
git push -u origin fix/disk-checker-consolidation
gh pr create --base main --title "refactor(checkers): consolidate disk module + align cleanup checkers" --body "$(cat <<'EOF'
## Summary
- Moves the five disk-related files into a new `apps/checkers/checkers/disk/` subpackage.
- Extracts `BaseDiskAnalyzer` so the three cleanup checkers (`disk_common`, `disk_macos`, `disk_linux`) shed ~80% copy-paste and become ~25-line declarations of target lists + recommendation rules.
- Adds concrete `large_file_targets` to macOS (`/Library/Application Support`, `/Users/Shared`) and Linux (`/srv`, `/opt`) — paths outside `~` so they don't double-walk `disk_common`'s `~` scan.
- `CHECKER_REGISTRY` keys are unchanged. Metrics shape is unchanged. `DiskChecker` (utilization) is moved byte-identical; only the file path changes.

Design doc: `docs/plans/2026-05-09-disk-checker-consolidation-design.md`

## Why
The three cleanup checkers were ~80% copy-paste of each other. Changes to the shared algorithm (#135's "globally sort across scan targets") had to land in three places. macOS and Linux had no `large_file_targets` purely because the duplication never got fixed. This PR removes the duplication so future algorithmic changes land once, and uses the alignment as the chance to give the platform-specific checkers their own large-file scanning.

## Test plan
- [x] `uv run pytest apps/checkers/` — full suite green
- [x] `uv run coverage report` — 100% on every file in `apps/checkers/checkers/disk/`
- [x] black / ruff / mypy clean
- [x] Live `check_health disk_common` / `check_health disk_macos` / `check_health disk` on a darwin host — output shape unchanged for `disk` and `disk_common`; `disk_macos` now reports `large_files` when `/Library/Application Support` or `/Users/Shared` has anything > 100 MB

## Commit structure
Bisect-friendly:
- `<sha>` `refactor(checkers): move disk files into disk/ subpackage` — pure file moves; no behavior change
- `<sha>` `refactor(checkers): extract BaseDiskAnalyzer; align disk cleanup checkers` — base class + declarative subclasses + new large_file_targets

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Two commits, both on this branch.** Don't squash locally; let GitHub squash-merge if the user prefers that style. The bisect-friendly split is real value.
- **Moves matter for `git mv`.** Use `git mv` for the file moves so git tracks them as renames (helps blame and review).
- **Patch paths are the trickiest part.** The migration is mechanical but easy to miss one. After updating, run the test files individually and watch for `AttributeError: module ... has no attribute ...` — that always means a patch path needs adjusting.
- **`sys` is imported in two places.** `disk.base` imports it for the metrics dict; subclasses import it for `_is_applicable`. Patches that change `sys.platform` to make `_is_applicable` return True must target the subclass module's `sys` binding (`disk.macos.sys` or `disk.linux.sys`). Patches that affect the metrics-recorded platform value would target `disk.base.sys` — but most tests don't care about that.
- **Don't change the public surface of `disk_utils`.** It's now `disk/utils.py`; same function names and signatures. Any code that does `from apps.checkers.checkers.disk_utils import ...` is broken — but verified that no such code exists outside the moved test file.
- **`__init__.py` re-exports are intentional.** They keep `from apps.checkers.checkers.disk import DiskCommonChecker` working as a stable shorthand. Don't remove them.
- **No threshold tuning, no flag additions, no metrics-shape changes.** This is a structural PR; behavior preservation is the contract for non-`large_files` paths. The new `large_file_targets` for macOS/Linux are the only intentional behavioral addition.
- **If a multi-target sort test from PR #135 fails** during the migration, it's almost certainly a patch path issue — those tests work because `_collect()` in the base sorts each list. Verify the patch points at `disk.base.scan_directory`.