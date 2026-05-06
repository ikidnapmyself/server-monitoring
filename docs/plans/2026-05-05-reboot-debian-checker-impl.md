---
title: "Reboot Debian Checker Implementation Plan"
parent: Plans
---

# `reboot_debian` Checker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a stateless `reboot_debian` checker that detects Debian-family hosts with a pending reboot (`/var/run/reboot-required`) and surfaces it as a WARNING. Auto-resolution and "pending since" semantics are handled by the existing alerts layer — no per-checker state.

**Architecture:** New `BaseChecker` subclass in `apps/checkers/checkers/reboot_debian.py`. Three private module-level helpers (`_is_debian_family`, `_flag_present`, `_read_pkgs`) keep tests scoped — same pattern as `disk_linux` patching `scan_directory` / `find_old_files`. Registered in `CHECKER_REGISTRY`. No new dependencies, no migrations.

**Tech Stack:** Python 3, Django, `pathlib`, `pytest`, `unittest.mock`, the existing `BaseChecker` and `CheckAlertBridge`.

**Branch:** `add-reboot-debian-checker` (already cut, design doc committed).

**Reference:** [Design Doc](2026-05-05-reboot-debian-checker-design.md)

---

## Pre-flight

Before starting, confirm:

```bash
git rev-parse --abbrev-ref HEAD
# Expected: add-reboot-debian-checker

uv run pytest apps/checkers/_tests/checkers/ -q
# Expected: all existing checker tests pass — establishes the baseline
```

If the branch is wrong, `git checkout add-reboot-debian-checker`. If existing tests fail, stop and investigate before adding new code.

Also note these conventions enforced by the project:

- 100% branch coverage required per PR (CLAUDE.md). Verify at the end with `uv run coverage run -m pytest apps/checkers/ && uv run coverage report --include="apps/checkers/checkers/reboot_debian.py" -m`.
- All file paths must be absolute (CLAUDE.md). The hard-coded `Path("/var/run/...")` literals already comply.
- Pre-commit hooks run `black`, `ruff`, `pytest`, `mypy`. Don't bypass with `--no-verify`.

---

## Task 1: Skeleton + registry wiring

**Files:**
- Create: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/checkers/__init__.py`
- Create: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write the failing registry test**

Create `apps/checkers/_tests/checkers/test_reboot_debian.py` with:

```python
"""Tests for the Debian reboot-required checker."""

from django.test import TestCase


class RebootDebianRegistryTests(TestCase):
    """Tests that the checker is wired into the registry."""

    def test_registered_in_checker_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        self.assertIs(CHECKER_REGISTRY["reboot_debian"], RebootDebianChecker)

    def test_exported_from_package(self):
        from apps.checkers.checkers import RebootDebianChecker

        self.assertEqual(RebootDebianChecker.name, "reboot_debian")
```

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py -v
```

Expected: ImportError / KeyError on `RebootDebianChecker` / `CHECKER_REGISTRY["reboot_debian"]`.

**Step 3: Create the skeleton module**

Create `apps/checkers/checkers/reboot_debian.py`:

```python
"""Debian-family reboot-required checker.

Detects /var/run/reboot-required (set by update-notifier-common after
APT installs kernel/libc/systemd updates that require a reboot) and
surfaces it as a WARNING. Stateless — alert lifecycle (open / update /
auto-resolve) is handled by apps.alerts.check_integration.CheckAlertBridge.

See docs/plans/2026-05-05-reboot-debian-checker-design.md for the rationale.
"""

import sys
from pathlib import Path

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

REBOOT_FLAG = Path("/var/run/reboot-required")
PKGS_FILE = Path("/var/run/reboot-required.pkgs")
OS_RELEASE = Path("/etc/os-release")


class RebootDebianChecker(BaseChecker):
    """Report WARNING when a Debian-family host has a pending reboot."""

    name = "reboot_debian"

    def check(self) -> CheckResult:
        # Implemented incrementally in subsequent tasks.
        return self._make_result(
            status=CheckStatus.OK,
            message="No reboot required",
            metrics={"reboot_required": False},
        )
```

**Step 4: Update the package `__init__.py`**

Modify `apps/checkers/checkers/__init__.py`:

- Add import: `from apps.checkers.checkers.reboot_debian import RebootDebianChecker`
- Add to `__all__`: `"RebootDebianChecker"`
- Add to `CHECKER_REGISTRY`: `"reboot_debian": RebootDebianChecker,`

**Step 5: Run the registry tests**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py -v
```

Expected: both registry tests pass.

**Step 6: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/checkers/__init__.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): scaffold reboot_debian checker + registry wiring"
```

---

## Task 2: Non-Linux platform skip

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing tests**

Append to the test file:

```python
from unittest.mock import patch


class RebootDebianCheckerPlatformTests(TestCase):
    """Platform gating tests."""

    def _get_checker(self):
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        return RebootDebianChecker()

    @patch("apps.checkers.checkers.reboot_debian.sys")
    def test_skipped_on_macos(self, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "darwin"
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["platform"], "darwin")
        self.assertEqual(result.metrics["reboot_required"], False)

    @patch("apps.checkers.checkers.reboot_debian.sys")
    def test_skipped_on_windows(self, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "win32"
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["platform"], "win32")
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerPlatformTests -v
```

Expected: AssertionError (message says "No reboot required", not "not Linux").

**Step 3: Implement the platform gate**

Replace `RebootDebianChecker.check()` in `reboot_debian.py`:

```python
def check(self) -> CheckResult:
    if sys.platform != "linux":
        return self._make_result(
            status=CheckStatus.OK,
            message="Skipped: not Linux",
            metrics={
                "platform": sys.platform,
                "distro_id": "",
                "reboot_required": False,
                "pending_packages": [],
                "pending_package_count": 0,
            },
        )
    # Linux path implemented in subsequent tasks.
    return self._make_result(
        status=CheckStatus.OK,
        message="No reboot required",
        metrics={
            "platform": sys.platform,
            "distro_id": "",
            "reboot_required": False,
            "pending_packages": [],
            "pending_package_count": 0,
        },
    )
```

**Step 4: Run the platform tests**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerPlatformTests -v
```

Expected: both tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): skip reboot_debian on non-Linux platforms"
```

---

## Task 3: `_is_debian_family()` helper (os-release parsing)

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing tests**

Append to the test file:

```python
class IsDebianFamilyTests(TestCase):
    """Tests for the _is_debian_family() helper."""

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_missing_os_release(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = False
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_unreadable_os_release(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("permission denied")
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_debian_via_id(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=debian\nVERSION="12 (bookworm)"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "debian")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_ubuntu_via_id(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=ubuntu\nID_LIKE=debian\n'
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "ubuntu")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_derivative_via_id_like(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=linuxmint\nID_LIKE="ubuntu debian"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "linuxmint")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_non_debian(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=fedora\nID_LIKE="rhel"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "fedora")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_handles_quoted_values_and_blank_lines(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = (
            "\n"
            "NAME=\"Ubuntu\"\n"
            "ID='ubuntu'\n"
            "# comment-style line without =\n"
            "PRETTY_NAME=\"Ubuntu 22.04\"\n"
        )
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "ubuntu")
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::IsDebianFamilyTests -v
```

Expected: ImportError on `_is_debian_family`.

**Step 3: Implement the helper**

Add to `reboot_debian.py` (above the class):

```python
def _is_debian_family() -> tuple[bool, str]:
    """Return (is_debian_family, distro_id) by reading /etc/os-release.

    Detects Debian, Ubuntu, and any derivative that sets ID_LIKE=debian
    (Mint, Pop!_OS, Kali, Raspbian, etc.). Returns (False, "") on missing
    or unreadable os-release — the caller should treat that as "not
    applicable, skip with OK".
    """
    if not OS_RELEASE.exists():
        return False, ""
    try:
        content = OS_RELEASE.read_text()
    except OSError:
        return False, ""

    fields: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().strip('"').strip("'")

    distro_id = fields.get("ID", "").lower()
    id_like = fields.get("ID_LIKE", "").lower().split()
    is_debian = distro_id in {"debian", "ubuntu"} or "debian" in id_like
    return is_debian, distro_id
```

**Step 4: Run the helper tests**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::IsDebianFamilyTests -v
```

Expected: all 7 tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): add _is_debian_family() os-release parser"
```

---

## Task 4: Distro gate (skip on non-Debian-family Linux)

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing tests**

Append to `RebootDebianCheckerPlatformTests`:

```python
@patch("apps.checkers.checkers.reboot_debian.sys")
@patch("apps.checkers.checkers.reboot_debian._is_debian_family")
def test_skipped_on_non_debian_linux(self, mock_distro, mock_sys):
    from apps.checkers.checkers.base import CheckStatus

    mock_sys.platform = "linux"
    mock_distro.return_value = (False, "fedora")
    result = self._get_checker().check()

    self.assertEqual(result.status, CheckStatus.OK)
    self.assertIn("not Debian-family", result.message)
    self.assertIn("fedora", result.message)
    self.assertEqual(result.metrics["distro_id"], "fedora")

@patch("apps.checkers.checkers.reboot_debian.sys")
@patch("apps.checkers.checkers.reboot_debian._is_debian_family")
def test_skipped_when_os_release_undetected(self, mock_distro, mock_sys):
    from apps.checkers.checkers.base import CheckStatus

    mock_sys.platform = "linux"
    mock_distro.return_value = (False, "")
    result = self._get_checker().check()

    self.assertEqual(result.status, CheckStatus.OK)
    self.assertIn("cannot determine distro", result.message)
    self.assertEqual(result.metrics["distro_id"], "")
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerPlatformTests -v
```

Expected: AssertionError on the new tests (current `check()` doesn't call `_is_debian_family`).

**Step 3: Wire the distro gate**

Replace `check()` in `reboot_debian.py`:

```python
def check(self) -> CheckResult:
    if sys.platform != "linux":
        return self._skip(reason="not Linux", distro_id="")

    is_debian, distro_id = _is_debian_family()
    if not is_debian:
        if distro_id:
            reason = f"not Debian-family ({distro_id})"
        else:
            reason = "cannot determine distro"
        return self._skip(reason=reason, distro_id=distro_id)

    # Reboot-required path implemented in subsequent tasks.
    return self._make_result(
        status=CheckStatus.OK,
        message="No reboot required",
        metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
    )

def _skip(self, *, reason: str, distro_id: str) -> CheckResult:
    return self._make_result(
        status=CheckStatus.OK,
        message=f"Skipped: {reason}",
        metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
    )

def _metrics(
    self,
    *,
    distro_id: str,
    reboot_required: bool,
    packages: list[str],
) -> dict:
    return {
        "platform": sys.platform,
        "distro_id": distro_id,
        "reboot_required": reboot_required,
        "pending_packages": packages,
        "pending_package_count": len(packages),
    }
```

**Step 4: Run the tests**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerPlatformTests -v
```

Expected: all 4 platform tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): skip reboot_debian on non-Debian Linux distros"
```

---

## Task 5: OK when reboot flag absent

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing test**

Append a new test class:

```python
class RebootDebianCheckerStatusTests(TestCase):
    """Tests for the OK / WARNING result paths."""

    def _get_checker(self):
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        return RebootDebianChecker()

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    @patch("apps.checkers.checkers.reboot_debian._flag_present")
    def test_ok_when_flag_absent(self, mock_flag, mock_distro, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "linux"
        mock_distro.return_value = (True, "ubuntu")
        mock_flag.return_value = False
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("No reboot required", result.message)
        self.assertEqual(result.metrics["reboot_required"], False)
        self.assertEqual(result.metrics["distro_id"], "ubuntu")
```

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerStatusTests::test_ok_when_flag_absent -v
```

Expected: ImportError on `_flag_present`.

**Step 3: Add the `_flag_present` helper and wire it in**

Add to `reboot_debian.py`:

```python
def _flag_present() -> bool:
    """Return True iff /var/run/reboot-required exists."""
    return REBOOT_FLAG.exists()
```

Update `check()` so the Debian-family branch consults `_flag_present()`:

```python
    is_debian, distro_id = _is_debian_family()
    if not is_debian:
        ...

    if not _flag_present():
        return self._make_result(
            status=CheckStatus.OK,
            message="No reboot required",
            metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
        )

    # WARNING path implemented in the next task.
    return self._make_result(
        status=CheckStatus.OK,
        message="No reboot required",
        metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
    )
```

**Step 4: Run the test**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerStatusTests -v
```

Expected: passes.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): reboot_debian returns OK when flag file absent"
```

---

## Task 6: WARNING when reboot flag present, no `.pkgs`

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing test**

Append to `RebootDebianCheckerStatusTests`:

```python
@patch("apps.checkers.checkers.reboot_debian.sys")
@patch("apps.checkers.checkers.reboot_debian._is_debian_family")
@patch("apps.checkers.checkers.reboot_debian._flag_present")
@patch("apps.checkers.checkers.reboot_debian._read_pkgs")
def test_warning_when_flag_present_no_pkgs(
    self, mock_pkgs, mock_flag, mock_distro, mock_sys
):
    from apps.checkers.checkers.base import CheckStatus

    mock_sys.platform = "linux"
    mock_distro.return_value = (True, "debian")
    mock_flag.return_value = True
    mock_pkgs.return_value = []
    result = self._get_checker().check()

    self.assertEqual(result.status, CheckStatus.WARNING)
    self.assertEqual(result.message, "Reboot required")
    self.assertEqual(result.metrics["reboot_required"], True)
    self.assertEqual(result.metrics["pending_packages"], [])
    self.assertEqual(result.metrics["pending_package_count"], 0)
```

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerStatusTests::test_warning_when_flag_present_no_pkgs -v
```

Expected: ImportError on `_read_pkgs`.

**Step 3: Add `_read_pkgs` stub and the WARNING branch**

Add to `reboot_debian.py`:

```python
def _read_pkgs() -> list[str]:
    """Return pending package names from /var/run/reboot-required.pkgs.

    Returns [] when the file is missing or unreadable. Strips whitespace
    and skips blank lines.
    """
    if not PKGS_FILE.exists():
        return []
    # Reading + filtering implemented in the next task.
    return []
```

Update the final branch of `check()`:

```python
    pending_packages = _read_pkgs()
    if pending_packages:
        message = f"Reboot required ({len(pending_packages)} pending packages)"
    else:
        message = "Reboot required"

    return self._make_result(
        status=CheckStatus.WARNING,
        message=message,
        metrics=self._metrics(
            distro_id=distro_id,
            reboot_required=True,
            packages=pending_packages,
        ),
    )
```

**Step 4: Run the test**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerStatusTests -v
```

Expected: all 3 status tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): reboot_debian raises WARNING when flag file present"
```

---

## Task 7: `_read_pkgs()` parses package list

**Files:**
- Modify: `apps/checkers/checkers/reboot_debian.py`
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

**Step 1: Write failing tests**

Append a new test class:

```python
class ReadPkgsTests(TestCase):
    """Tests for the _read_pkgs() helper."""

    @patch("apps.checkers.checkers.reboot_debian.PKGS_FILE")
    def test_returns_empty_when_file_missing(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _read_pkgs

        mock_path.exists.return_value = False
        self.assertEqual(_read_pkgs(), [])

    @patch("apps.checkers.checkers.reboot_debian.PKGS_FILE")
    def test_parses_package_list(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _read_pkgs

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "linux-image-generic\nlibc6\n"
        self.assertEqual(_read_pkgs(), ["linux-image-generic", "libc6"])

    @patch("apps.checkers.checkers.reboot_debian.PKGS_FILE")
    def test_strips_blank_lines_and_whitespace(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _read_pkgs

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "linux-image\n\n  libc6  \n\n"
        self.assertEqual(_read_pkgs(), ["linux-image", "libc6"])

    @patch("apps.checkers.checkers.reboot_debian.PKGS_FILE")
    def test_returns_empty_on_oserror(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _read_pkgs

        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("permission denied")
        self.assertEqual(_read_pkgs(), [])
```

Also append to `RebootDebianCheckerStatusTests`:

```python
@patch("apps.checkers.checkers.reboot_debian.sys")
@patch("apps.checkers.checkers.reboot_debian._is_debian_family")
@patch("apps.checkers.checkers.reboot_debian._flag_present")
@patch("apps.checkers.checkers.reboot_debian._read_pkgs")
def test_warning_with_packages(
    self, mock_pkgs, mock_flag, mock_distro, mock_sys
):
    from apps.checkers.checkers.base import CheckStatus

    mock_sys.platform = "linux"
    mock_distro.return_value = (True, "ubuntu")
    mock_flag.return_value = True
    mock_pkgs.return_value = ["linux-image-generic", "libc6"]
    result = self._get_checker().check()

    self.assertEqual(result.status, CheckStatus.WARNING)
    self.assertIn("2 pending packages", result.message)
    self.assertEqual(
        result.metrics["pending_packages"], ["linux-image-generic", "libc6"]
    )
    self.assertEqual(result.metrics["pending_package_count"], 2)
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::ReadPkgsTests -v
```

Expected: 3 of 4 fail (parsing tests fail, missing-file passes already).

**Step 3: Implement parsing**

Replace `_read_pkgs()`:

```python
def _read_pkgs() -> list[str]:
    """Return pending package names from /var/run/reboot-required.pkgs.

    Returns [] when the file is missing or unreadable. Strips whitespace
    and skips blank lines.
    """
    if not PKGS_FILE.exists():
        return []
    try:
        content = PKGS_FILE.read_text()
    except OSError:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]
```

**Step 4: Run the tests**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py -v
```

Expected: all tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/reboot_debian.py \
        apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "feat(checkers): parse reboot-required.pkgs into package list"
```

---

## Task 8: Catch-all UNKNOWN via `BaseChecker.run()`

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

This task is verification-only — no production code change. `BaseChecker.run()` already wraps `check()` and converts unhandled exceptions to UNKNOWN. We add the test to lock that behavior in for `reboot_debian` and to satisfy the branch-coverage requirement.

**Step 1: Write the test**

Append a new test class:

```python
class RebootDebianCheckerErrorTests(TestCase):
    """Tests for the UNKNOWN error path."""

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    @patch("apps.checkers.checkers.reboot_debian._flag_present")
    def test_unexpected_exception_returns_unknown(
        self, mock_flag, mock_distro, mock_sys
    ):
        from apps.checkers.checkers.base import CheckStatus
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        mock_sys.platform = "linux"
        mock_distro.return_value = (True, "ubuntu")
        mock_flag.side_effect = RuntimeError("boom")

        result = RebootDebianChecker().run()  # .run(), not .check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("boom", result.message)
        self.assertEqual(result.error, "boom")
```

**Step 2: Run the test**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianCheckerErrorTests -v
```

Expected: passes immediately (behavior comes from `BaseChecker.run()`).

**Step 3: Commit**

```bash
git add apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "test(checkers): cover UNKNOWN path for reboot_debian.run()"
```

---

## Task 9: Alert lifecycle integration test

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_reboot_debian.py`

This is the highest-value test — proves the design's "open on first observation, stable across the streak, auto-resolves on reboot" property end-to-end through `CheckAlertBridge` and a real (in-memory) DB.

**Step 1: Write the test**

Append a new test class:

```python
class RebootDebianAlertIntegrationTests(TestCase):
    """End-to-end test of the alert lifecycle through CheckAlertBridge."""

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    @patch("apps.checkers.checkers.reboot_debian._flag_present")
    @patch("apps.checkers.checkers.reboot_debian._read_pkgs")
    def test_warning_then_resolved_keeps_started_at_stable(
        self, mock_pkgs, mock_flag, mock_distro, mock_sys
    ):
        """Three sequential runs: open → update → resolve.

        Asserts:
        - First WARNING run opens an Alert with started_at = T1.
        - Second WARNING run (still pending, packages changed) updates the
          same Alert; started_at stays at T1; annotations refresh.
        - OK run (post-reboot) resolves the Alert; ended_at populated;
          parent Incident auto-resolves.
        """
        from apps.alerts.check_integration import CheckAlertBridge
        from apps.alerts.models import Alert, AlertStatus, Incident, IncidentStatus
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        mock_sys.platform = "linux"
        mock_distro.return_value = (True, "ubuntu")

        bridge = CheckAlertBridge(hostname="test-host")
        checker = RebootDebianChecker()

        # Run 1: reboot becomes pending.
        mock_flag.return_value = True
        mock_pkgs.return_value = ["linux-image-generic"]
        bridge.process_check_result(checker.check())

        alert = Alert.objects.get()
        self.assertEqual(alert.status, AlertStatus.FIRING)
        original_started_at = alert.started_at
        self.assertIn("linux-image-generic", str(alert.annotations))
        incident = Incident.objects.get()
        self.assertEqual(incident.status, IncidentStatus.OPEN)

        # Run 2: still pending, follow-up upgrade adds another package.
        mock_pkgs.return_value = ["linux-image-generic", "libc6"]
        bridge.process_check_result(checker.check())

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.FIRING)
        self.assertEqual(alert.started_at, original_started_at)  # stable
        self.assertIn("libc6", str(alert.annotations))  # current pkg list
        self.assertEqual(Alert.objects.count(), 1)  # no duplicate

        # Run 3: reboot completes, flag file gone.
        mock_flag.return_value = False
        mock_pkgs.return_value = []
        bridge.process_check_result(checker.check())

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED)
        self.assertIsNotNone(alert.ended_at)
        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
```

**Step 2: Run the test**

```bash
uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py::RebootDebianAlertIntegrationTests -v
```

Expected: passes. If it fails on a labels/annotations field shape, inspect the failing assertion and adjust the assertion to match what `CheckAlertBridge` actually writes — *do not* change the bridge.

**Step 3: Commit**

```bash
git add apps/checkers/_tests/checkers/test_reboot_debian.py
git commit -m "test(checkers): cover reboot_debian alert lifecycle end-to-end"
```

---

## Task 10: Coverage verification + final cleanup

**Files:** None directly — verification step. Add tests only if coverage gaps surface.

**Step 1: Run full project test suite**

```bash
uv run pytest apps/checkers/ -v
```

Expected: every test in the checkers app passes, no warnings introduced.

**Step 2: Run coverage on the new module**

```bash
uv run coverage run -m pytest apps/checkers/_tests/checkers/test_reboot_debian.py
uv run coverage report --include="apps/checkers/checkers/reboot_debian.py" -m
```

Expected: `apps/checkers/checkers/reboot_debian.py` reports **100%** with no missing branches.

If coverage is below 100%, the report's `Missing` column shows the uncovered lines. Add a targeted test for each — common gaps to anticipate:
- A particular `os-release` parse branch (e.g., line with `=` but empty value)
- The `if pending_packages` truthiness branch in the message construction

**Step 3: Run linters and formatters**

```bash
uv run ruff check apps/checkers/checkers/reboot_debian.py apps/checkers/_tests/checkers/test_reboot_debian.py
uv run black --check apps/checkers/checkers/reboot_debian.py apps/checkers/_tests/checkers/test_reboot_debian.py
uv run mypy apps/checkers/checkers/reboot_debian.py
```

Expected: all clean. If `ruff` or `black` flag anything, run them with `--fix` / without `--check` to apply.

**Step 4: Smoke-test via management command**

```bash
uv run python manage.py run_check reboot_debian
```

Expected: a single check execution that emits one of:
- "Skipped: not Linux" on macOS (the developer's local env)
- "Skipped: not Debian-family (...)" on a non-Debian Linux
- "No reboot required" on a Debian/Ubuntu host with no pending reboot
- "Reboot required (...)" on a Debian/Ubuntu host that's pending

If the command raises an unhandled exception, that's a regression — investigate before proceeding.

**Step 5: Final commit (only if anything changed)**

```bash
git status
# If coverage/lint changes were needed:
git add apps/checkers/_tests/checkers/test_reboot_debian.py apps/checkers/checkers/reboot_debian.py
git commit -m "test(checkers): close coverage gaps in reboot_debian"
```

---

## Wrap-up: open the PR

After all tasks are green and committed:

```bash
git push -u origin add-reboot-debian-checker
gh pr create --title "feat(checkers): add reboot_debian checker for Debian-family hosts" \
  --body "$(cat <<'EOF'
## Summary
- Adds `reboot_debian` checker that detects `/var/run/reboot-required` on Debian/Ubuntu/derivatives and surfaces it as a WARNING.
- Stateless — alert lifecycle (open / update / auto-resolve on reboot) is delivered by the existing `CheckAlertBridge`, consistent with every other checker in the codebase.
- See `docs/plans/2026-05-05-reboot-debian-checker-design.md` for the full design rationale, including rejected alternatives.

## Test plan
- [ ] `uv run pytest apps/checkers/_tests/checkers/test_reboot_debian.py -v` passes (incl. end-to-end alert lifecycle test).
- [ ] `uv run coverage report --include="apps/checkers/checkers/reboot_debian.py" -m` reports 100%.
- [ ] `uv run python manage.py run_check reboot_debian` returns sensible output on the developer's host.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

The PR opens against `main`. CI runs the same pre-commit hooks plus the full test suite. Reviewer: anyone with checker-app context.

---

## Done

The implementation lands a single file (`apps/checkers/checkers/reboot_debian.py`, ~70 lines), one registry edit, and one test file (~280 lines covering all branches + the integration test). No migrations, no dependencies, no impact on other checkers.

If a future need for RHEL/Fedora support comes up, this checker can either be renamed to `reboot_required` with a second branch added, or a sibling `reboot_rhel` checker can join the registry. The design doc captures both options as deferred.