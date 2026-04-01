---
title: "2026-03-13 Remove check_and_alert, Absorb into run_pipeline"
parent: Plans
nav_order: 79739686
---

# Remove `check_and_alert`, Absorb into `run_pipeline`

## Problem

`check_and_alert` bypasses the orchestration pipeline entirely — it runs checkers and creates alerts/incidents directly via `CheckAlertBridge` with no `PipelineRun`/`StageExecution` audit trail. All executions must be tracked through orchestration.

## Decision

Remove `check_and_alert` command. Absorb its unique CLI flags into `run_pipeline`. Keep `CheckAlertBridge` (used by orchestration executors and tasks).

## What Gets Deleted

- `apps/alerts/management/commands/check_and_alert.py`

## What Gets Kept

- `apps/alerts/check_integration.py` (`CheckAlertBridge`) — used by `apps/orchestration/executors.py` and `apps/alerts/tasks.py`
- `apps/alerts/_tests/test_check_integration.py`

## What Gets Modified

### `run_pipeline` command — new flags

These flags apply when the checkers stage runs:

| Flag | Type | Description |
|------|------|-------------|
| `--checkers` | nargs+ | Select specific checkers (e.g., `cpu memory disk`) |
| `--warning-threshold` | float | Override warning threshold for all checkers |
| `--critical-threshold` | float | Override critical threshold for all checkers |
| `--hostname` | str | Override hostname in alert labels |
| `--label KEY=VALUE` | append | Additional labels for alerts |
| `--no-incidents` | flag | Skip incident creation |

### References to update

| File | Change |
|------|--------|
| `bin/setup_cron.sh` | `check_and_alert --json` → `run_pipeline --checks-only --json` |
| `bin/setup_aliases.sh` | Remove `check-and-alert` alias, or redirect to `run_pipeline --checks-only` |
| `bin/cli.sh` | Update menu to use `run_pipeline --checks-only` |
| `bin/README.md` | Remove `check_and_alert` row, update docs |
| `apps/alerts/README.md` | Remove `check_and_alert` usage section |
| `apps/checkers/README.md` | Update reference |
| `docs/plans/*.md` | Update any references |
| `docs/Architecture.md` | Update if referenced |
| `README.md` | Update if referenced |

### How flags flow through orchestration

The new flags on `run_pipeline` get passed into the pipeline payload context so that `CheckAlertBridge` (called by the checkers executor) picks them up. The executor already reads `checker_names` and `checker_configs` from context — we extend this to include `hostname`, `labels`, and `no_incidents`.