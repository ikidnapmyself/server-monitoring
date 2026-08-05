# RAID (mdadm) Health Checker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `RaidChecker` that reports Linux software-RAID (mdadm) health by parsing `/proc/mdstat`, with no sudo and no subprocess.

**Architecture:** A stateless, Linux-gated `BaseChecker` subclass modeled on `apps/checkers/checkers/reboot_debian.py`. A pure `parse_mdstat(text)` function separates parsing from side effects. State-based severity mapping (no numeric thresholds): degraded/inactive → CRITICAL, rebuilding → WARNING, healthy/empty → OK. Registered in `CHECKER_REGISTRY` so it auto-flows into `check_health`, `run_check`, and the pipeline.

**Tech Stack:** Python 3, Django, pytest/pytest-django, `BaseChecker`/`CheckResult`/`CheckStatus` from `apps.checkers.checkers.base`.

**Design doc:** `docs/plans/2026-08-05-raid-mdstat-checker-design.md`

**Reference before starting:**
- `apps/checkers/checkers/reboot_debian.py` — closest existing pattern (platform gating, `_make_result`, metrics dict, skip-as-OK).
- `apps/checkers/checkers/base.py` — `BaseChecker`, `CheckResult`, `CheckStatus`, `_make_result`.
- `apps/checkers/checkers/__init__.py` — `CHECKER_REGISTRY` registration.
- `apps/checkers/_tests/checkers/test_reboot_debian.py` — test style/fixtures for a state-based checker.

**Conventions (from AGENTS.md):** absolute imports; line length 100; Black + Ruff clean; 100% branch coverage on changed code; no subprocess here so the `shutil.which`/argv rules do not apply.

---

## Reference: `/proc/mdstat` shapes to parse

Healthy raid1:
```
Personalities : [raid1]
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
```

Degraded raid5 (one disk missing) + faulty flag:
```
md1 : active raid5 sdd1[3](F) sdc1[1] sde1[0]
      3906764800 blocks super 1.2 [3/2] [UU_]

unused devices: <none>
```

Rebuild in progress:
```
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/1] [U_]
      [==========>..........]  recovery = 50.0% (976/1953) finish=10.0min speed=100000K/sec

unused devices: <none>
```

Inactive array:
```
md2 : inactive sdf1[0](S)
      1953382464 blocks

unused devices: <none>
```

Empty:
```
Personalities :
unused devices: <none>
```

Key fields per array: name (`md0`), state (`active`/`inactive`), level (`raid1`/`raid5`/`raid0`/`linear`/... or `None` when inactive), `[N/M]` (active/total), `[UU_]` (per-slot up map), `(F)`/`(S)` device flags, and an optional `recovery`/`resync`/`reshape`/`check` progress line with a percent.

---

## Task 1: Parser skeleton + healthy-array case

**Files:**
- Create: `apps/checkers/checkers/raid.py`
- Test: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing test**

```python
"""Tests for the mdadm/RAID checker."""

from apps.checkers.checkers.raid import ArrayState, parse_mdstat

HEALTHY = """Personalities : [raid1]
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""


def test_parse_healthy_raid1():
    arrays = parse_mdstat(HEALTHY)
    assert len(arrays) == 1
    a = arrays[0]
    assert isinstance(a, ArrayState)
    assert a.name == "md0"
    assert a.level == "raid1"
    assert a.state == "active"
    assert a.active_devices == 2
    assert a.total_devices == 2
    assert a.failed == []
    assert a.rebuilding is False
    assert a.resync_percent is None
    assert a.is_degraded() is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: FAIL — `ModuleNotFoundError` / cannot import `raid`.

**Step 3: Write minimal implementation**

```python
"""Linux software-RAID (mdadm) health checker.

Reads /proc/mdstat (world-readable, no root, no subprocess) and reports
degraded/rebuilding md arrays. Stateless and Linux-gated, modeled on
apps.checkers.checkers.reboot_debian.

See docs/plans/2026-08-05-raid-mdstat-checker-design.md for rationale.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

MDSTAT = Path("/proc/mdstat")

# "md0 : active raid1 sdb1[1] sda1[0]"  /  "md2 : inactive sdf1[0](S)"
_ARRAY_RE = re.compile(r"^(md\d+)\s*:\s*(\S+)\s+(.*)$")
# "[2/2]" -> active/total
_COUNTS_RE = re.compile(r"\[(\d+)/(\d+)\]")
# "[UU_]" -> per-slot up map
_UPMAP_RE = re.compile(r"\[([U_]+)\]")
# recovery/resync/reshape/check progress with percent
_PROGRESS_RE = re.compile(
    r"\b(recovery|resync|reshape|check)\s*=\s*([\d.]+)%", re.IGNORECASE
)
# device tokens like "sdd1[3](F)" -> name, flag
_DEVICE_RE = re.compile(r"(\S+?)\[\d+\](\([FS]\))?")


@dataclass
class ArrayState:
    """Parsed state of a single md array."""

    name: str
    state: str
    level: str | None = None
    active_devices: int | None = None
    total_devices: int | None = None
    failed: list[str] = field(default_factory=list)
    rebuilding: bool = False
    resync_percent: float | None = None

    def is_degraded(self) -> bool:
        if self.state != "active":
            return True
        if self.failed:
            return True
        if self.active_devices is not None and self.total_devices is not None:
            return self.active_devices < self.total_devices
        return False


def parse_mdstat(text: str) -> list[ArrayState]:
    """Parse /proc/mdstat text into a list of ArrayState."""
    arrays: list[ArrayState] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _ARRAY_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name, state, rest = m.group(1), m.group(2), m.group(3)
        level: str | None = None
        tokens = rest.split()
        if tokens and tokens[0].startswith("raid") or tokens and tokens[0] in {"linear", "multipath", "faulty"}:
            level = tokens[0]
        failed = [
            dm.group(1)
            for dm in _DEVICE_RE.finditer(rest)
            if dm.group(2) == "(F)"
        ]
        array = ArrayState(name=name, state=state, level=level, failed=failed)
        # Look at the following continuation lines for this block.
        j = i + 1
        while j < len(lines) and lines[j].startswith(" "):
            block = lines[j]
            counts = _COUNTS_RE.search(block)
            if counts:
                array.active_devices = int(counts.group(1))
                array.total_devices = int(counts.group(2))
            upmap = _UPMAP_RE.search(block)
            if upmap and "_" in upmap.group(1):
                # Any down slot not already captured as a faulty device.
                array.failed = array.failed or [f"slot:{k}" for k, c in enumerate(upmap.group(1)) if c == "_"]
            prog = _PROGRESS_RE.search(block)
            if prog:
                array.rebuilding = True
                array.resync_percent = float(prog.group(2))
            j += 1
        arrays.append(array)
        i = j
    return arrays
```

> Note: the `level` detection line above is intentionally refined in Task 5 (inactive arrays). Keep it simple to pass Task 1 first; the inactive test will force the correction.

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/raid.py apps/checkers/_tests/checkers/test_raid.py
git commit -m "feat(checkers): parse healthy /proc/mdstat into ArrayState"
```

---

## Task 2: Degraded raid5 (missing device + faulty flag) → is_degraded

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing test**

```python
DEGRADED = """md1 : active raid5 sdd1[3](F) sdc1[1] sde1[0]
      3906764800 blocks super 1.2 [3/2] [UU_]

unused devices: <none>
"""


def test_parse_degraded_raid5():
    arrays = parse_mdstat(DEGRADED)
    assert len(arrays) == 1
    a = arrays[0]
    assert a.name == "md1"
    assert a.level == "raid5"
    assert a.active_devices == 2
    assert a.total_devices == 3
    assert "sdd1" in a.failed
    assert a.is_degraded() is True
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_parse_degraded_raid5 -v`
Expected: PASS or FAIL — if the faulty-device regex already captures `sdd1`, this may pass immediately. If it FAILs, fix `_DEVICE_RE`/`failed` extraction minimally until it passes.

**Step 3: Adjust implementation if needed**

Ensure `(F)` devices are captured into `failed` by name (`sdd1`). No change expected beyond Task 1 if the regex is correct.

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py apps/checkers/checkers/raid.py
git commit -m "test(checkers): degraded raid5 parses failed device + counts"
```

---

## Task 3: Rebuild-in-progress parsing

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing test**

```python
REBUILDING = """md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/1] [U_]
      [==========>..........]  recovery = 50.0% (976/1953) finish=10.0min speed=100000K/sec

unused devices: <none>
"""


def test_parse_rebuilding():
    a = parse_mdstat(REBUILDING)[0]
    assert a.rebuilding is True
    assert a.resync_percent == 50.0
    assert a.active_devices == 1
    assert a.total_devices == 2
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_parse_rebuilding -v`
Expected: PASS if `_PROGRESS_RE` matches `recovery`; otherwise FAIL — fix minimally.

**Step 3: Adjust if needed** — ensure `recovery|resync|reshape|check` percent is captured.

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py apps/checkers/checkers/raid.py
git commit -m "test(checkers): rebuild progress parses percent + rebuilding flag"
```

---

## Task 4: Empty mdstat → no arrays

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing test**

```python
EMPTY = """Personalities :
unused devices: <none>
"""


def test_parse_empty():
    assert parse_mdstat(EMPTY) == []
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_parse_empty -v`
Expected: PASS (no `mdX` lines match). If FAIL, fix `_ARRAY_RE` anchoring.

**Step 3: Adjust if needed.**

**Step 4: Run to verify it passes** — Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py
git commit -m "test(checkers): empty mdstat yields no arrays"
```

---

## Task 5: Inactive array → degraded, level None

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`
- Modify: `apps/checkers/checkers/raid.py`

**Step 1: Write the failing test**

```python
INACTIVE = """md2 : inactive sdf1[0](S)
      1953382464 blocks

unused devices: <none>
"""


def test_parse_inactive():
    a = parse_mdstat(INACTIVE)[0]
    assert a.state == "inactive"
    assert a.level is None
    assert a.is_degraded() is True
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_parse_inactive -v`
Expected: FAIL — the naive `level` line from Task 1 may misassign `sdf1[0](S)`'s token as level, or the operator precedence bug in the `if` makes `level` wrong.

**Step 3: Fix the level detection**

Replace the fragile Task-1 level line with a clear, correctly-parenthesized version. In `parse_mdstat`:

```python
        level: str | None = None
        tokens = rest.split()
        if tokens:
            first = tokens[0]
            if first.startswith("raid") or first in {"linear", "multipath", "faulty"}:
                level = first
```

Inactive arrays list a device token first (e.g. `sdf1[0](S)`), which does not match, so `level` stays `None`. `is_degraded()` already returns True when `state != "active"`.

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: PASS (all parser tests).

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py apps/checkers/checkers/raid.py
git commit -m "fix(checkers): inactive md array parses with level=None, degraded"
```

---

## Task 6: `RaidChecker.check()` — healthy → OK

**Files:**
- Modify: `apps/checkers/checkers/raid.py`
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing test**

```python
import sys

import pytest

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.raid import RaidChecker


@pytest.fixture
def force_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def test_check_healthy_ok(monkeypatch, force_linux):
    monkeypatch.setattr(
        "apps.checkers.checkers.raid._read_mdstat", lambda: HEALTHY
    )
    result = RaidChecker().check()
    assert result.status == CheckStatus.OK
    assert result.metrics["array_count"] == 1
    assert result.metrics["degraded_arrays"] == []
    assert result.metrics["rebuilding_arrays"] == []
    assert result.metrics["arrays"][0]["name"] == "md0"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_check_healthy_ok -v`
Expected: FAIL — `RaidChecker` / `_read_mdstat` not defined.

**Step 3: Add `_read_mdstat`, `RaidChecker`, and result-building to `raid.py`**

```python
def _read_mdstat() -> str | None:
    """Return /proc/mdstat contents, or None if absent/unreadable."""
    try:
        return MDSTAT.read_text()
    except OSError:
        return None


def _array_to_dict(a: ArrayState) -> dict:
    return {
        "name": a.name,
        "level": a.level,
        "state": a.state,
        "active_devices": a.active_devices,
        "total_devices": a.total_devices,
        "failed": a.failed,
        "rebuilding": a.rebuilding,
        "resync_percent": a.resync_percent,
    }


class RaidChecker(BaseChecker):
    """Report degraded/rebuilding Linux software-RAID (mdadm) arrays."""

    name = "raid"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        text = _read_mdstat()
        if text is None:
            return self._skip("mdstat unavailable")

        arrays = parse_mdstat(text)
        if not arrays:
            return self._make_result(
                status=CheckStatus.OK,
                message="No software RAID arrays",
                metrics=self._metrics([]),
            )

        degraded = [a for a in arrays if a.is_degraded()]
        rebuilding = [a for a in arrays if a.rebuilding and not a.is_degraded()]

        if degraded:
            status = CheckStatus.CRITICAL
            message = f"Degraded RAID array(s): {', '.join(a.name for a in degraded)}"
        elif rebuilding:
            status = CheckStatus.WARNING
            message = f"RAID array(s) rebuilding: {', '.join(a.name for a in rebuilding)}"
        else:
            status = CheckStatus.OK
            message = f"All {len(arrays)} RAID array(s) healthy"

        return self._make_result(
            status=status, message=message, metrics=self._metrics(arrays)
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([]),
        )

    def _metrics(self, arrays: list[ArrayState]) -> dict:
        return {
            "platform": sys.platform,
            "array_count": len(arrays),
            "arrays": [_array_to_dict(a) for a in arrays],
            "degraded_arrays": [a.name for a in arrays if a.is_degraded()],
            "rebuilding_arrays": [
                a.name for a in arrays if a.rebuilding and not a.is_degraded()
            ],
        }
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_check_healthy_ok -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/raid.py apps/checkers/_tests/checkers/test_raid.py
git commit -m "feat(checkers): RaidChecker.check maps healthy mdstat to OK"
```

---

## Task 7: `check()` severity — degraded → CRITICAL, rebuild → WARNING

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing tests**

```python
def test_check_degraded_critical(monkeypatch, force_linux):
    monkeypatch.setattr(
        "apps.checkers.checkers.raid._read_mdstat", lambda: DEGRADED
    )
    result = RaidChecker().check()
    assert result.status == CheckStatus.CRITICAL
    assert "md1" in result.metrics["degraded_arrays"]
    assert "md1" in result.message


def test_check_rebuilding_warning(monkeypatch, force_linux):
    # A [2/1] rebuild is also degraded; use a rebuild that is NOT degraded:
    # counts [2/2] but with an active recovery line (e.g. a check scrub).
    scrub = (
        "md0 : active raid1 sdb1[1] sda1[0]\n"
        "      1953382464 blocks super 1.2 [2/2] [UU]\n"
        "      [==>..................]  check = 12.3% (240/1953) finish=5min speed=100000K/sec\n"
        "\nunused devices: <none>\n"
    )
    monkeypatch.setattr("apps.checkers.checkers.raid._read_mdstat", lambda: scrub)
    result = RaidChecker().check()
    assert result.status == CheckStatus.WARNING
    assert result.metrics["rebuilding_arrays"] == ["md0"]
```

**Step 2: Run to verify it fails/passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -k "degraded_critical or rebuilding_warning" -v`
Expected: PASS with the Task 6 implementation (degraded precedence over rebuilding is already coded). If FAIL, adjust precedence in `check()`.

**Step 3: Adjust if needed** — degraded must take precedence over rebuilding.

**Step 4: Run to verify it passes** — Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py
git commit -m "test(checkers): degraded->CRITICAL, scrub-rebuild->WARNING"
```

---

## Task 8: Skip paths — non-Linux + missing mdstat → OK

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_raid.py`

**Step 1: Write the failing tests**

```python
def test_check_non_linux_skips(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = RaidChecker().check()
    assert result.status == CheckStatus.OK
    assert "not Linux" in result.message
    assert result.metrics["array_count"] == 0


def test_check_missing_mdstat_skips(monkeypatch, force_linux):
    monkeypatch.setattr("apps.checkers.checkers.raid._read_mdstat", lambda: None)
    result = RaidChecker().check()
    assert result.status == CheckStatus.OK
    assert "mdstat unavailable" in result.message


def test_read_mdstat_returns_none_on_oserror(monkeypatch):
    def boom():
        raise OSError("nope")

    monkeypatch.setattr(
        "apps.checkers.checkers.raid.MDSTAT.read_text", lambda *a, **k: boom()
    )
    from apps.checkers.checkers.raid import _read_mdstat

    assert _read_mdstat() is None
```

> Note: adjust the `_read_mdstat` OSError test to whatever cleanly triggers the `except OSError` branch (e.g. `monkeypatch.setattr(Path, "read_text", ...)`), so the branch is covered.

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -k skip -v`
Expected: PASS for skip tests with Task 6 code; ensure the OSError branch test passes.

**Step 3: Adjust if needed.**

**Step 4: Run full file**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/_tests/checkers/test_raid.py
git commit -m "test(checkers): non-linux + missing mdstat skip as OK"
```

---

## Task 9: Register in CHECKER_REGISTRY

**Files:**
- Modify: `apps/checkers/checkers/__init__.py`

**Step 1: Write the failing test**

Add to `apps/checkers/_tests/checkers/test_raid.py`:

```python
def test_registered_in_registry():
    from apps.checkers.checkers import CHECKER_REGISTRY, RaidChecker as ExportedRaid

    assert CHECKER_REGISTRY["raid"] is ExportedRaid
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_registered_in_registry -v`
Expected: FAIL — `raid` not in registry / `RaidChecker` not exported.

**Step 3: Register**

In `apps/checkers/checkers/__init__.py`:
- Add import: `from apps.checkers.checkers.raid import RaidChecker`
- Add `"RaidChecker",` to `__all__`
- Add `"raid": RaidChecker,` to `CHECKER_REGISTRY` (keep alphabetical-ish grouping near the other single checkers).

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/checkers/_tests/checkers/test_raid.py::test_registered_in_registry -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/__init__.py apps/checkers/_tests/checkers/test_raid.py
git commit -m "feat(checkers): register raid checker in CHECKER_REGISTRY"
```

---

## Task 10: Coverage, lint, format, security, docs

**Files:**
- Modify: `apps/checkers/README.md`
- Modify: `AGENTS.md` (checker list row), `apps/checkers/AGENTS.md` if it enumerates checkers

**Step 1: Branch coverage on changed code**

Run:
```bash
uv run coverage run -m pytest apps/checkers/_tests/checkers/test_raid.py
uv run coverage report -m --include="apps/checkers/checkers/raid.py"
```
Expected: 100% branch coverage on `raid.py`. Add targeted tests for any uncovered line/branch (e.g. `linear`/`multipath` level token, upmap `_` fallback for `failed`).

**Step 2: Format + lint + type + security**

Run:
```bash
uv run black apps/checkers/checkers/raid.py apps/checkers/_tests/checkers/test_raid.py
uv run ruff check apps/checkers/checkers/raid.py apps/checkers/_tests/checkers/test_raid.py --fix
uv run mypy apps/checkers/checkers/raid.py
uv run bandit -r apps/checkers/checkers/raid.py -c pyproject.toml
```
Expected: all clean (bandit: no issues — no subprocess).

**Step 3: Docs**

- Add a `raid` row to the checker table in `apps/checkers/README.md` (source: `/proc/mdstat`, no sudo, OK/WARNING/CRITICAL mapping).
- Update the checker enumeration in `AGENTS.md` (Core apps table lists checkers: add `raid`).

**Step 4: Full checker suite regression**

Run: `uv run pytest apps/checkers/_tests/`
Expected: PASS (no regressions).

**Step 5: Commit**

```bash
git add apps/checkers/README.md AGENTS.md apps/checkers/AGENTS.md
git commit -m "docs(checkers): document raid (mdadm) checker"
```

---

## Task 11: Manual verification

**Step 1: Registry + command wiring**

Run:
```bash
uv run python manage.py check_health --list
```
Expected: `raid` appears in the checker list.

**Step 2: Run the checker standalone**

Run: `uv run python manage.py run_check raid`
Expected (on non-Linux dev machine): OK, message "Skipped: not Linux".
Expected (on a Linux host with no md arrays): OK, "No software RAID arrays".

**Step 3: Confirm final state**

Run: `uv run pytest apps/checkers/_tests/ && uv run black . --check && uv run ruff check .`
Expected: all green.

---

## Acceptance criteria

- `RaidChecker` registered as `"raid"`; visible in `check_health --list`; runnable via `run_check raid`.
- Parser handles: healthy, degraded (missing device + `(F)`), rebuild/scrub, inactive, empty.
- Severity: degraded/inactive → CRITICAL, rebuild-only → WARNING, healthy/empty → OK; non-Linux + missing mdstat → OK skip.
- 100% branch coverage on `raid.py`; `black`/`ruff`/`bandit`/`pytest` clean.
- Docs updated (README + AGENTS checker list).
