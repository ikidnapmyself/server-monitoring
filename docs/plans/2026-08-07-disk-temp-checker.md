---
title: "disk_temp Checker Implementation Plan"
parent: Plans
---

# disk_temp Checker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Linux-gated `DiskTempChecker` (`disk_temp`) that reports the hottest disk temperature from `psutil.sensors_temperatures()` (`drivetemp`/`nvme` chips), no sudo, plus a shared `_sensors.py` helper reused later by `cpu_temp`.

**Architecture:** Numeric-threshold checker (like `cpu`/`memory`) using `BaseChecker._determine_status()`. A pure `parse_temps(raw, chip_allowlist)` in `apps/checkers/checkers/_sensors.py` separates parsing from the `sensors_temperatures()` I/O for testability. Worst-disk-wins status; sensor-absent → skip-as-OK; non-Linux → skip-as-OK.

**Tech Stack:** Python 3, Django, pytest, `psutil`, `BaseChecker`/`CheckResult`/`CheckStatus`.

**Design doc:** `docs/plans/2026-08-07-thermal-io-checkers-design.md`

**COMMIT POLICY (this branch):** Do NOT commit per task. After each task, `git add` the changed files to keep them staged; a single commit is made at the very end of all work. No intermediate git history.

**Reference before starting:**
- `apps/checkers/checkers/raid.py` + `_tests/checkers/test_raid.py` — closest pattern: Linux gate, skip-as-OK, pure-parse-separate-from-IO, worst-wins, `force_linux` test fixture.
- `apps/checkers/checkers/cpu.py` — numeric-threshold + `psutil` + `_determine_status` + metrics dict shape.
- `apps/checkers/checkers/base.py` — `BaseChecker`, `_determine_status`, `_make_result`, thresholds.
- `apps/checkers/checkers/__init__.py` — `CHECKER_REGISTRY` registration.

**Conventions:** absolute imports; line length 100; black+ruff clean; 100% branch coverage on changed lines.

## `psutil.sensors_temperatures()` facts

- Returns `dict[str, list[shwtemp]]`; `shwtemp` fields: `(label, current, high, critical)` (°C floats; `high`/`critical` may be `None`).
- Disk temps appear under chip keys `drivetemp` (SATA/NVMe via the `drivetemp` module) and `nvme`. CPU temps under `coretemp`/`k10temp`/etc. (not this checker).
- **The function does not exist on macOS** — so code must never call it off-Linux (the platform gate handles this), and tests must patch with `create=True`.

---

## Task 1: `_sensors.py` — `parse_temps` (pure) + `read_temps`

**Files:**
- Create: `apps/checkers/checkers/_sensors.py`
- Test: `apps/checkers/_tests/checkers/test_sensors.py`

**Step 1: Write the failing test**

```python
"""Tests for the shared hwmon sensor helper."""

from apps.checkers.checkers._sensors import TempReading, parse_temps


def _raw():
    # Mimics psutil.sensors_temperatures(): {chip: [shwtemp(label,current,high,critical)]}
    from collections import namedtuple

    s = namedtuple("shwtemp", ["label", "current", "high", "critical"])
    return {
        "drivetemp": [s("sda", 58.0, 60.0, 65.0), s("sdb", 40.0, 60.0, 65.0)],
        "nvme": [s("Composite", 44.0, None, 84.0)],
        "coretemp": [s("Package id 0", 70.0, 100.0, 100.0)],  # not a disk chip
    }


def test_parse_selects_only_allowlisted_chips():
    readings = parse_temps(_raw(), {"drivetemp", "nvme"})
    labels = {r.label for r in readings}
    assert labels == {"sda", "sdb", "Composite"}
    assert all(isinstance(r, TempReading) for r in readings)
    sda = next(r for r in readings if r.label == "sda")
    assert sda.chip == "drivetemp"
    assert sda.current == 58.0


def test_parse_drops_none_and_nonpositive_current():
    from collections import namedtuple

    s = namedtuple("shwtemp", ["label", "current", "high", "critical"])
    raw = {"drivetemp": [s("sda", None, None, None), s("sdb", 0.0, None, None),
                         s("sdc", 55.0, None, None)]}
    readings = parse_temps(raw, {"drivetemp"})
    assert [r.label for r in readings] == ["sdc"]


def test_parse_empty_when_no_matching_chip():
    assert parse_temps({"coretemp": []}, {"drivetemp", "nvme"}) == []
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_sensors.py -v`
Expected: FAIL — cannot import `_sensors`.

**Step 3: Implement**

```python
"""Shared hwmon temperature helper (psutil.sensors_temperatures).

parse_temps() is pure (no I/O) so it is unit-testable against captured
fixtures; read_temps() performs the psutil call and delegates to it. Reused by
the disk_temp and (later) cpu_temp checkers.
"""

from dataclasses import dataclass

import psutil


@dataclass
class TempReading:
    """One hwmon temperature sensor reading, in degrees Celsius."""

    chip: str
    label: str
    current: float
    high: float | None = None
    critical: float | None = None


def parse_temps(raw: dict, chip_allowlist: set[str]) -> list[TempReading]:
    """Filter a sensors_temperatures() dict to allowlisted chips.

    Drops readings whose ``current`` is None or <= 0 (unpopulated sensors).
    """
    readings: list[TempReading] = []
    for chip, entries in raw.items():
        if chip not in chip_allowlist:
            continue
        for e in entries:
            if e.current is None or e.current <= 0:
                continue
            readings.append(
                TempReading(
                    chip=chip,
                    label=e.label or chip,
                    current=float(e.current),
                    high=e.high,
                    critical=e.critical,
                )
            )
    return readings


def read_temps(chip_allowlist: set[str]) -> list[TempReading]:
    """Read current temperatures for allowlisted chips.

    Returns [] when sensors_temperatures() is unavailable (non-Linux) or empty.
    """
    fn = getattr(psutil, "sensors_temperatures", None)
    if fn is None:
        return []
    return parse_temps(fn(), chip_allowlist)
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_sensors.py -v`
Expected: PASS.

**Step 5: Stage (do NOT commit)**

```bash
git add apps/checkers/checkers/_sensors.py apps/checkers/_tests/checkers/test_sensors.py
```

---

## Task 2: `DiskTempChecker.check()` — healthy/warning/critical, worst-wins

**Files:**
- Create: `apps/checkers/checkers/disk_temp.py`
- Test: `apps/checkers/_tests/checkers/test_disk_temp.py`

**Step 1: Write the failing test**

```python
"""Tests for the disk temperature checker."""

import sys
from collections import namedtuple

import pytest

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk_temp import DiskTempChecker

_shwtemp = namedtuple("shwtemp", ["label", "current", "high", "critical"])


@pytest.fixture
def force_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def _patch_sensors(monkeypatch, raw):
    monkeypatch.setattr(
        "apps.checkers.checkers._sensors.psutil.sensors_temperatures",
        lambda: raw,
        raising=False,  # attribute may not exist on macOS
    )


def test_healthy_disk_is_ok(monkeypatch, force_linux):
    _patch_sensors(monkeypatch, {"drivetemp": [_shwtemp("sda", 40.0, 60.0, 65.0)]})
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.OK
    assert result.metrics["hottest_c"] == 40.0
    assert result.metrics["hottest_disk"] == "sda"
    assert result.metrics["disk_count"] == 1


def test_worst_disk_drives_warning(monkeypatch, force_linux):
    _patch_sensors(monkeypatch, {"drivetemp": [
        _shwtemp("sda", 40.0, None, None),
        _shwtemp("sdb", 57.0, None, None),  # warn >= 55
    ]})
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.WARNING
    assert result.metrics["hottest_disk"] == "sdb"
    assert "sdb" in result.message


def test_critical_disk(monkeypatch, force_linux):
    _patch_sensors(monkeypatch, {"nvme": [_shwtemp("Composite", 62.0, None, 84.0)]})
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.CRITICAL
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_temp.py -v`
Expected: FAIL — cannot import `disk_temp`.

**Step 3: Implement**

```python
"""Disk temperature checker for Linux (hwmon: drivetemp / nvme).

Reads disk temperatures via psutil.sensors_temperatures() — no sudo, provided
the drivetemp kernel module (5.6+) / nvme sensors are loaded. Linux-gated;
skips as OK on non-Linux or when no disk sensors are present. SMART/smartctl
(needs root) is intentionally out of scope.

See docs/plans/2026-08-07-thermal-io-checkers-design.md.
"""

import sys

from apps.checkers.checkers._sensors import read_temps
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

DISK_CHIPS = {"drivetemp", "nvme"}


class DiskTempChecker(BaseChecker):
    """Report the hottest disk temperature (°C)."""

    name = "disk_temp"
    warning_threshold = 55.0
    critical_threshold = 60.0

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        readings = read_temps(DISK_CHIPS)
        if not readings:
            return self._skip("no disk temperature sensors")

        hottest = max(readings, key=lambda r: r.current)
        status = self._determine_status(hottest.current)
        message = (
            f"Hottest disk {hottest.current:.1f}°C "
            f"({hottest.label}) [{hottest.chip}]"
        )
        return self._make_result(
            status=status,
            message=message,
            metrics=self._metrics(readings, hottest),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([], None),
        )

    def _metrics(self, readings, hottest) -> dict:
        return {
            "platform": sys.platform,
            "disk_count": len(readings),
            "hottest_c": hottest.current if hottest else None,
            "hottest_disk": hottest.label if hottest else None,
            "disks": [
                {"disk": r.label, "chip": r.chip, "temp_c": r.current,
                 "high_c": r.high, "critical_c": r.critical}
                for r in readings
            ],
        }
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_temp.py -v`
Expected: PASS.

**Step 5: Stage (do NOT commit)**

```bash
git add apps/checkers/checkers/disk_temp.py apps/checkers/_tests/checkers/test_disk_temp.py
```

---

## Task 3: Skip paths — non-Linux + sensor-absent → OK

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_disk_temp.py`

**Step 1: Write the failing/needed tests**

```python
def test_non_linux_skips_ok(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.OK
    assert "not Linux" in result.message
    assert result.metrics["disk_count"] == 0
    assert result.metrics["hottest_c"] is None


def test_no_sensors_skips_ok(monkeypatch, force_linux):
    _patch_sensors(monkeypatch, {})  # no chips at all
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.OK
    assert "no disk temperature sensors" in result.message


def test_only_nonpositive_readings_skips_ok(monkeypatch, force_linux):
    _patch_sensors(monkeypatch, {"drivetemp": [_shwtemp("sda", 0.0, None, None)]})
    result = DiskTempChecker().check()
    assert result.status == CheckStatus.OK
    assert "no disk temperature sensors" in result.message
```

**Step 2–4: Run** — `uv run pytest apps/checkers/_tests/checkers/test_disk_temp.py -v`
Expected: PASS (Task 2 impl already handles these; if the missing-`sensors_temperatures` path needs coverage, add a test that patches `read_temps` to return `[]`).

**Step 5: Stage**

```bash
git add apps/checkers/_tests/checkers/test_disk_temp.py
```

---

## Task 4: Register + docs + coverage/lint

**Files:**
- Modify: `apps/checkers/checkers/__init__.py`
- Modify: `apps/checkers/README.md`, `AGENTS.md`, `apps/checkers/AGENTS.md`
- Test: `apps/checkers/_tests/checkers/test_disk_temp.py`

**Step 1: Registry test (failing)**

```python
def test_registered_in_registry():
    from apps.checkers.checkers import CHECKER_REGISTRY
    from apps.checkers.checkers import DiskTempChecker as Exported

    assert CHECKER_REGISTRY["disk_temp"] is Exported
```

**Step 2: Register** in `apps/checkers/checkers/__init__.py`:
- `from apps.checkers.checkers.disk_temp import DiskTempChecker`
- add `"DiskTempChecker",` to `__all__`
- add `"disk_temp": DiskTempChecker,` to `CHECKER_REGISTRY`

**Step 3: Coverage + lint + security**

```bash
uv run coverage run -m pytest apps/checkers/_tests/checkers/test_disk_temp.py apps/checkers/_tests/checkers/test_sensors.py
uv run coverage report -m --include="*/apps/checkers/checkers/disk_temp.py,*/apps/checkers/checkers/_sensors.py"
uv run black apps/checkers/checkers/disk_temp.py apps/checkers/checkers/_sensors.py apps/checkers/_tests/checkers/test_disk_temp.py apps/checkers/_tests/checkers/test_sensors.py
uv run ruff check apps/checkers/checkers/disk_temp.py apps/checkers/checkers/_sensors.py --fix
uv run bandit -r apps/checkers/checkers/disk_temp.py apps/checkers/checkers/_sensors.py -c pyproject.toml
```
Expected: 100% branch coverage on both new modules; all clean. Add tests for any uncovered branch (e.g. `read_temps` when `sensors_temperatures` is absent → returns []).

**Step 4: Docs**

- `apps/checkers/README.md`: add a `disk_temp` row (source hwmon drivetemp/nvme, no sudo, warn 55 °C / crit 60 °C, skips as OK on non-Linux / no sensors; note `modprobe drivetemp` prerequisite).
- `AGENTS.md` core-apps checker list + `apps/checkers/AGENTS.md` OS-specific note: add `disk_temp`.

**Step 5: Full checker suite + stage**

```bash
uv run pytest apps/checkers/_tests/
git add apps/checkers/checkers/__init__.py apps/checkers/README.md AGENTS.md apps/checkers/AGENTS.md apps/checkers/_tests/checkers/test_disk_temp.py
```

---

## Final step (once, at end of all work): single commit

After all tasks pass and everything is staged:

```bash
git status   # review the staged set
# (User requested one commit at the end — commit everything together.)
```

## Acceptance criteria

- `disk_temp` registered; runnable via `run_check disk_temp`; visible in `check_health --list`.
- Worst-disk-wins OK/WARNING/CRITICAL at 55/60 °C; non-Linux and no-sensor paths skip as OK.
- `_sensors.py` `parse_temps` pure + unit-tested; reusable for `cpu_temp` later.
- 100% branch coverage on `disk_temp.py` + `_sensors.py`; black/ruff/bandit/pytest clean.
- Everything staged; one final commit.
