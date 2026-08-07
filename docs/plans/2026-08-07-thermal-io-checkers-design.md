---
title: "Thermal & IO Checkers (disk_temp, cpu_temp, io_strain)"
parent: Plans
---

# Thermal & IO Checkers Design

**Date:** 2026-08-07
**Status:** Approved (design)
**Stage:** diagnose (`apps.checkers`)
**Build order:** `disk_temp` first (recurring real pain on a bare-metal node), then `cpu_temp` and `io_strain`.

## Problem

One bare-metal node repeatedly runs its disks hot, and we have no visibility
into hardware temperature or IO saturation. We want checkers for disk
temperature, CPU temperature, and IO strain.

## Scope decision

**Three separate checkers**, not one combined checker: they measure different
things, in different units, with different thresholds and different data
sources. This matches the one-concern-one-checker pattern (`cpu`, `memory`,
`disk` are already separate) and the `BaseChecker` single warning/critical
threshold model.

- `disk_temp` — hottest disk temperature (°C)
- `cpu_temp` — hottest CPU package/core temperature (°C)
- `io_strain` — busiest disk utilization (% busy time)

## OS gating (not a choice — forced by data source)

All three read Linux-only data, so all are **Linux-gated** with skip-as-OK on
non-Linux, following the `raid` / `disk_linux` precedent:

- Temperatures: `psutil.sensors_temperatures()` reads `/sys/class/hwmon`
  (Linux only; the function is absent on macOS/Windows). No sudo.
- IO: `psutil.disk_io_counters(perdisk=True)` `busy_time` is a Linux field.

## Shared architecture

- **Pattern:** numeric-threshold checkers (like `cpu`/`memory`/`disk`) — read a
  value, map to OK/WARNING/CRITICAL via `BaseChecker._determine_status()` with
  per-checker thresholds. Thresholds stay overridable via
  `--warning-threshold` / `--critical-threshold` and the DB config path.
- **Worst-wins:** with multiple disks/sensors, the worst reading drives the
  status; every reading is included in `metrics` so the offending
  disk/sensor is identifiable.
- **Sensor-absent = skip-as-OK.** On cloud VMs / many VPS hosts,
  `sensors_temperatures()` returns `{}` and disks expose no `drivetemp`. When
  the relevant sensor is simply not present, return **OK** with a
  "no … sensors available" message — not UNKNOWN (which would spam every VM).
  The bare-metal node with the heat problem has sensors and gets real readings.
- **Layout:** flat files `cpu_temp.py`, `disk_temp.py`, `io_strain.py`, plus a
  small shared `_sensors.py` helper for the two temperature checkers (both
  parse `psutil.sensors_temperatures()`). Parsing is kept pure/separate from
  the check side effects for unit-testing.

## Per-checker specifics

| Checker | Source | Status driver | Default thresholds | Key metrics |
|---|---|---|---|---|
| `disk_temp` | `sensors_temperatures()` chips `drivetemp`, `nvme` | hottest disk | warn **55 °C**, crit **60 °C** | per-disk °C + label, hottest disk name, disk count |
| `cpu_temp` | `sensors_temperatures()` chips `coretemp`, `k10temp`, `zenpower`, `cpu_thermal` | max package/core temp | warn **80 °C**, crit **90 °C** | per-sensor °C, max, chip label |
| `io_strain` | `disk_io_counters(perdisk=True)`, sample `busy_time` over an interval | busiest disk %util | warn **80 %**, crit **95 %** | per-disk %util, r/w throughput & IOPS over the sample |

Thresholds are standard-ops defaults (disk 55/60 °C is common HDD/SSD guidance;
CPU 80/90 °C is conservative vs typical ~90–100 °C Tj limits). **Kept as-is.**

### `disk_temp` (first build)

- Read `psutil.sensors_temperatures()`; select entries whose chip key is in the
  disk allowlist (`drivetemp`, `nvme`). Each entry has a label (device) and
  `current` °C; also `high`/`critical` if the sensor exposes them (recorded in
  metrics, not used to override thresholds).
- Status: `_determine_status(hottest_current)`.
- Message e.g. `Hottest disk 58.0°C (sda) [drivetemp]`.
- **No-sudo caveat:** relies on the `drivetemp` kernel module (Linux 5.6+) /
  `nvme` sensors being loaded so temps appear in hwmon without root. SMART
  (`smartctl`, needs root) is out. If `drivetemp` isn't loaded → skip-OK
  ("no disk temperature sensors"). Document `modprobe drivetemp` (and persisting
  it) as the operational prerequisite.

### `io_strain`

- Take two `disk_io_counters(perdisk=True)` reads separated by a sample
  interval (default ~1 s, overridable like `cpu`'s `sample_interval`); per disk
  `%util = busy_time_delta / (elapsed_ms) × 100`, clamped to [0, 100].
- Status from the busiest disk's %util.

### `cpu_temp`

- Read `sensors_temperatures()`; select CPU chips (allowlist above); status from
  the max `current`.

## Error handling & edge cases

- Non-Linux → skip-OK.
- `sensors_temperatures()` missing / returns `{}` / no matching chips → skip-OK.
- Sensor `current` of `None` or `0.0` filtered out; if nothing valid remains →
  skip-OK.
- `disk_io_counters()` returns `None` (rare/containers) → skip-OK.
- `BaseChecker.run()` already wraps unexpected exceptions into UNKNOWN.

## Tests

Mock `psutil.sensors_temperatures()` / `disk_io_counters()` fixtures for each
checker: healthy, warning, critical, multi-disk/-sensor worst-wins,
sensor-absent skip, non-Linux skip, `None`/zero filtering, (io) two-sample
%util math. 100% branch coverage; pure parsing in `_sensors.py` unit-tested
directly.

## Wiring

Register `disk_temp` (then `cpu_temp`, `io_strain`) in `CHECKER_REGISTRY`
(auto-flows into `check_health`, `run_check`, pipeline). Docs rows in
`apps/checkers/README.md` and the checker lists in `AGENTS.md` /
`apps/checkers/AGENTS.md`.

## Acceptance criteria

- Each checker registered and runnable via `run_check <name>`.
- Correct worst-wins OK/WARNING/CRITICAL mapping against fixtures.
- Non-Linux and sensor-absent paths skip cleanly as OK.
- 100% branch coverage on changed code; `black`/`ruff`/`bandit`/`pytest` clean.

## Build phases

1. **`disk_temp`** + shared `_sensors.py` helper (this plan).
2. `cpu_temp` (reuses `_sensors.py`).
3. `io_strain`.
