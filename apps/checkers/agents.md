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
  - Some checkers (for example, `disk_macos`, `disk_linux`) are OS-specific and may use platform gating — early return OK with a skip message on unsupported OSes
- `apps/checkers/checks.py` — Django system checks (run with `manage.py check`)
- `apps/checkers/management/commands/` — commands like `check_health`, `run_check`, `preflight`
- `apps/checkers/models.py` — `CheckRun` (standalone mode audit trail)

## Boundary rules

- Pipeline mode: **do not** advance the pipeline or notify directly.
  - Only `apps.orchestration` transitions stages.
- Checkers **may** call external monitoring/vendor APIs as additional diagnostic inputs (when justified).
  - Examples: StatusCake checks, latest PagerDuty incidents/history, hosted uptime checks.
  - Requirements: timeouts, retries/backoff, clear failure modes, and no secret leakage in logs.
  - These integrations must **not** create incidents/alerts/notifications directly; they only enrich checker output.
- Prefer small, deterministic checkers; isolate external I/O and enforce timeouts.
- **Always use absolute paths**: Resolve all file/directory paths to absolute form via `pathlib.Path.resolve()` before use. Validate resolved paths against allowed directories when accepting user input.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.checkers`, admin should make it easy to:
- Inspect `CheckRun` history (filters by checker/status/hostname, search by trace_id)
- Review checker outputs and errors (as stored in models or orchestration output snapshots)
- Correlate standalone check runs vs pipeline stage executions (via trace/run identifiers)

## App layout rules (required)

- Any HTTP endpoints must live under `apps/checkers/views/` (endpoint/module-based).
- Tests must live under `apps/checkers/_tests/` and mirror the module tree.
  - Example: `checkers/cpu.py` → `_tests/checkers/test_cpu.py`
  - Example: `management/commands/check_health.py` → `_tests/management/commands/test_check_health.py`

## Management command contracts

### `preflight`

Runs all Django system checks grouped by tag with formatted output.

```bash
manage.py preflight                    # All checks, human output
manage.py preflight --only security    # Filter by tag(s)
manage.py preflight --json             # JSON output for CI
```

Input: None (reads Django system check registry)
Output (human): Grouped checks with OK/WARN/ERR/INFO levels + summary line
Output (JSON): `{ "groups": { "<tag>": { "checks": [...], "errors": N, "warnings": N } }, "summary": { "passed": N, "warnings": N, "errors": N } }`
Exit code: 0 always (uses Django's check framework, not custom exit codes)

Tag groups (in display order): security, environment, pipeline, crontab, migrations, database

## Doc vs code status

Tests have been migrated to `_tests/` (completed). Some code still uses monolithic `views.py`; migrate to `views/` package when touching related code.

## Security standards (audit-enforced)

Authoritative source: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/checkers/` section.

### Rules for new checkers
- **List-form argv only for subprocess.** Every `subprocess.run` / `subprocess.Popen` call MUST pass a list (e.g. `["du", "-sh", path]`), never a string. `shell=True` is forbidden.
- **Validate host / path constructor arguments.** If a checker accepts `host`, `path`, or any URL in `__init__`, call `validate_safe_url(host, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)` and `resolve_safe_path(path)` from `config.security` at construction time. Fail closed on invalid input.
- **Class-level `scan_targets` / `LOG_DIRECTORIES` constants are intentionally not kwargs.** If you need an admin to customise targets, route through the `IntelligenceProvider`/`CHECKER_CONFIG` DB layer or Django settings — never accept these as caller-supplied kwargs that would flow through `provider_config` (see [Finding 1](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md) for the `scan_paths` precedent).
- **External API integrations** (StatusCake, PagerDuty, etc.) MUST use `safe_urlopen` from `config.security.http`; raw `urllib.request` is banned by ruff `TID251`.
- **Timeouts on every outbound call.** No bare `urlopen(req)` without `timeout=`.

### Trust boundary discipline
- Pipeline-mode checker inputs (`hostname`, `checker_configs`, `labels`, `checks_only`) arrive from `/orchestration/pipeline/*` — treat them as untrusted even after API-key auth.
- Standalone CLI inputs (`run_check --paths`) are admin-trusted but still routed through `resolve_safe_path` for defence in depth.
- Never echo raw exception messages into HTTP responses; log via `logger.exception(..., extra={"trace_id": ...})`.

### Audit checks before merging
- [ ] No new `subprocess` call without list-form argv and an explicit `timeout=`.
- [ ] Any new constructor-accepted path/host validated through `config.security`.
- [ ] No new path-bearing kwarg added that flows from `provider_config` without being added to `apps.intelligence.providers.BLOCKED_CONFIG_KEYS`.
- [ ] Run `uv run pytest apps/checkers/_tests/` and confirm scope-narrowing tests still pass.
