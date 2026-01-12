# apps.checkers — Agent Notes

This file contains **app-local** guidance for working in `apps/checkers/`.

## Role in the pipeline

Stage: **diagnose**

Responsibilities:
- Run diagnostics/health checks for an incident (pipeline mode)
- Or run checks standalone via management commands (standalone mode)

Output contract (to orchestrator):
- `{ checks: [...], timings, errors, checker_output_ref }`

## Key modules

- `apps/checkers/checkers/` — checker implementations
  - Registry lives in `apps/checkers/checkers/__init__.py` (`CHECKER_REGISTRY`)
- `apps/checkers/checks.py` — Django system checks (run with `manage.py check`)
- `apps/checkers/management/commands/` — commands like `check_health`, `check_and_alert`
- `apps/checkers/models.py` — `CheckRun` (standalone mode audit trail)

## Boundary rules

- Pipeline mode: **do not** advance the pipeline or notify directly.
  - Only `apps.orchestration` transitions stages.
- Checkers **may** call external monitoring/vendor APIs as additional diagnostic inputs (when justified).
  - Examples: StatusCake checks, latest PagerDuty incidents/history, hosted uptime checks.
  - Requirements: timeouts, retries/backoff, clear failure modes, and no secret leakage in logs.
  - These integrations must **not** create incidents/alerts/notifications directly; they only enrich checker output.
- Prefer small, deterministic checkers; isolate external I/O and enforce timeouts.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.checkers`, admin should make it easy to:
- Inspect `CheckRun` history (filters by checker/status/hostname, search by trace_id)
- Review checker outputs and errors (as stored in models or orchestration output snapshots)
- Correlate standalone check runs vs pipeline stage executions (via trace/run identifiers)

## App layout rules (required)

- Any HTTP endpoints must live under `apps/checkers/views/` (endpoint/module-based).
- Tests must live under `apps/checkers/tests/` and mirror the module tree.
  - Example: `checkers/cpu.py` → `tests/checkers/test_cpu.py`
  - Example: `management/commands/check_health.py` → `tests/management/commands/test_check_health.py`

## Doc vs code status

Some code still uses `views.py` / `tests.py`. This doc defines the **target layout** going forward.
