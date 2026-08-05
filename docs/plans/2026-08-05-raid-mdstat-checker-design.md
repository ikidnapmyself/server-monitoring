---
title: "RAID (mdadm) Health Checker Design"
parent: Plans
---

# RAID (mdadm) Health Checker Design

**Date:** 2026-08-05
**Status:** Approved (design)
**Stage:** diagnose (`apps.checkers`)

## Problem

We have no visibility into Linux software-RAID health across the fleet. A
degraded `md` array (a disk dropped from a mirror or parity set) is silent
until a second failure causes data loss. We want a checker that surfaces
degraded / rebuilding arrays as monitoring signals.

## Constraints

- **No sudo.** Anything requiring root is out of scope. This rules out
  hardware-RAID vendor CLIs (`storcli`, `megacli`, `ssacli`) and most
  `mdadm --detail` fields.
- Must match the established checker pattern (`BaseChecker`, `CHECKER_REGISTRY`,
  platform gating, parse-separate-from-side-effects).

## Scope decision

**In:** Linux software RAID (mdadm), read from `/proc/mdstat`.
`/proc/mdstat` is world-readable — no root, no subprocess, no vendor tools.
This is the closest analogue to the existing `reboot_debian` checker.

**Out (YAGNI, per AGENTS.md scope discipline):**

- Hardware RAID controllers — require root; no-sudo constraint excludes them.
- ZFS / btrfs pools — different tooling, subprocess-dependent, tools may be
  absent. Can be a separate checker later if a concrete need appears.

## Component

New file: `apps/checkers/checkers/raid.py`

- `RaidChecker(BaseChecker)`, `name = "raid"`, registered as `"raid"` in
  `apps/checkers/checkers/__init__.py` (`CHECKER_REGISTRY`).
- Pure parse function `parse_mdstat(text: str) -> list[ArrayState]` kept
  separate from `check()` side effects, so it is unit-testable against
  captured `/proc/mdstat` fixtures.
- State-based checker: it does **not** use the numeric
  `warning_threshold` / `critical_threshold` machinery (like `reboot_debian`).

### Data source

- Read `/proc/mdstat` (world-readable).
- Non-Linux (`sys.platform != "linux"`) → early return **OK** with a skip
  message.
- `/proc/mdstat` missing or unreadable → early return **OK** with a skip
  message ("no software RAID / mdstat unavailable").

### Parsing

For each `mdX` block, extract:

- array name (`md0`), RAID level (`raid1`), array state (`active` / `inactive`)
- device count `[N/M]` → `active_devices` (N), `total_devices` (M)
- device up/down map `[UU_]` → list of failed slots (`_`)
- per-device flags: `(F)` faulty, `(S)` spare
- resync / recovery / reshape progress line → `rebuilding` bool + percent

## Severity mapping

| Array condition | Status |
|---|---|
| All arrays present, all devices `U`, no rebuild | OK |
| Rebuild / resync / recovery / reshape in progress (otherwise healthy) | WARNING |
| Degraded — a device is `_`, `(F)` faulty, or `active < total`, array still running | CRITICAL |
| Array `inactive` / failed | CRITICAL |
| No md arrays at all | OK ("no software RAID arrays") |

Rationale: a **degraded-but-running** array is one failure away from data
loss, so it is CRITICAL, not WARNING. A rebuild is transient/self-healing, so
WARNING. The overall `CheckResult` status is the worst status across all
arrays.

## Metrics

Attached to the `CheckResult` for admin/audit and the intelligence stage:

```json
{
  "platform": "linux",
  "array_count": 2,
  "arrays": [
    {
      "name": "md0", "level": "raid1", "state": "active",
      "active_devices": 2, "total_devices": 2, "failed": [],
      "rebuilding": false, "resync_percent": null
    }
  ],
  "degraded_arrays": ["md1"],
  "rebuilding_arrays": []
}
```

## Error handling & edge cases

- Non-Linux → OK skip.
- `/proc/mdstat` absent or unreadable (`OSError`) → OK skip.
- `unused devices: <none>` / no `mdX` blocks → OK ("no software RAID arrays").
- Malformed / unexpected lines → parsed defensively; unknown state does not
  crash the checker (`BaseChecker.run` also wraps exceptions into UNKNOWN).

## Tests

`apps/checkers/_tests/checkers/test_raid.py`, driving `parse_mdstat` +
`check()` against captured fixtures:

- healthy raid1 (`[2/2] [UU]`)
- degraded raid5 (`[3/2] [UU_]`) → CRITICAL
- rebuild in progress → WARNING
- faulty `(F)` device → CRITICAL
- inactive array → CRITICAL
- empty (`unused devices: <none>`) → OK
- non-Linux skip → OK
- missing / unreadable `/proc/mdstat` → OK

Target: 100% branch coverage on changed code.

## Wiring

- Register `"raid"` in `CHECKER_REGISTRY` → auto-flows into `check_health`,
  `run_check raid`, and pipeline execution.
- Docs: add a row to `apps/checkers/README.md` and the checker list in
  `AGENTS.md` / app `AGENTS.md`.

No new settings, env vars, models, or migrations. No subprocess (so the
`shutil.which` / list-argv / bandit audit rules do not apply).

## Acceptance criteria

- `RaidChecker` registered and runnable via `run_check raid`.
- Correct OK / WARNING / CRITICAL mapping for the fixture cases above.
- Non-Linux and missing-file paths skip cleanly as OK.
- Tests pass with 100% branch coverage on changed lines.
- `black`, `ruff`, `pytest`, `bandit` clean.
