# apps.checkers — Agent Notes

This file contains **app-local** guidance for working in `apps/checkers/`.

## Role in the pipeline

Stage: **diagnose**

Responsibilities:
- Run diagnostics/health checks for an incident (pipeline mode)
- Or run checks standalone via management commands (standalone mode)

Output contract (to orchestrator):
- `{ checks: [...], timings, errors, checker_output_ref }`, plus `alert_id`,
  `incident_id`, `alert_fingerprint` and `material_incident_ids` — a pipeline-mode CHECK
  writes real `Alert`/`Incident` rows through `CheckAlertBridge`, it does not only report.

**CHECK's scope is the incident, not the registry.** For an incident run the stage runs
the checkers named by the distinct `checker` labels on that incident's alerts, filtered to
`CHECKER_REGISTRY`. An incident that names no checkers runs none — it does not sweep the
whole machine. That is why `CheckAlertBridge.run_checks_and_alert` distinguishes
`checker_names=None` ("every checker", what a caller with no opinion passes) from
`checker_names=[]` ("none", what an incident naming no checkers passes). Never collapse
the two. See `apps/orchestration/AGENTS.md`.

## Key modules

- `apps/checkers/checkers/` — checker implementations
  - Registry lives in `apps/checkers/checkers/__init__.py` (`CHECKER_REGISTRY`)
  - Some checkers (for example, `disk_macos`, `disk_linux`, `raid`, `disk_temp`, `cpu_temp`, `io_strain`, `listening_ports`) are OS-specific and may use platform gating — early return OK with a skip message on unsupported OSes (`raid` reads Linux `/proc/mdstat`; `disk_temp`/`cpu_temp` read Linux hwmon via `psutil.sensors_temperatures()`; `io_strain` samples `psutil.disk_io_counters` `busy_time`; `listening_ports` reads `psutil.net_connections` (`/proc/net`, no root on Linux only) — all skip as OK on non-linux / when the data is unavailable)
- `apps/checkers/checks.py` — Django system checks (run with `manage.py check`)
- `apps/checkers/management/commands/` — commands like `check_health`, `run_check`, `preflight`
- `apps/checkers/models.py` — `CheckRun` (standalone mode audit trail); `PreflightRun` + `PreflightCheck` (node-local persisted history of `preflight` runs — counts, `overall_status`, and per-check `level`/`message`/`hint`; **no hub-push**). Browse under Checkers → Preflight runs (readonly, with a readonly check inline).
- `apps/checkers/admin_charts.py` — `render_sparkline(points, markers=…)`: self-contained inline SVG (no JS/CDN/`xmlns`, `currentColor` for theme). Used for the disk-usage sparkline on the Node admin page; reuse it for any admin trend chart.

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

### `check_health`

Runs the registered checkers (or a named subset) and prints the results.

```bash
manage.py check_health                 # All checkers, human output
manage.py check_health cpu memory      # Named checkers only
manage.py check_health --json          # JSON output for CI
manage.py check_health --no-alert      # skip alert recording (print only)
manage.py check_health --no-notify     # record + analyse, run no NOTIFY stage
```

**Alert recording:** by default each run hands its results to `CheckAlertBridge`,
which writes an alert (and incident) per firing checker keyed on this machine's
`instance_id` and registers the machine as a `Node`; healthy checkers write nothing.

**Orchestration:** this is the synchronous local entrypoint. After recording, the
command calls `apps.orchestration.intake.enqueue_for(..., sync=True)` — one
`PipelineRun` per materially changed incident, drained before the command returns —
so a single-machine install gets the analysis with no hub, no cron and nobody
draining the inbox. `--no-notify` travels with those runs, for looking at a machine
without paging anyone.

A recording *or* orchestration failure is reported on stderr and swallowed, so
output (including `--json` on stdout) and the exit code are unaffected. `--no-alert`
restores print-only behaviour: nothing written, nothing enqueued.

### `preflight`

Runs all Django system checks grouped by tag with formatted output.

```bash
manage.py preflight                    # All checks, human output
manage.py preflight --only security    # Filter by tag(s)
manage.py preflight --json             # JSON output for CI
manage.py preflight --no-save          # skip persistence (print only)
```

**Persistence:** by default each run writes a `PreflightRun` (+ child `PreflightCheck`s) so history is visible in the admin; `--no-save` restores print-only behaviour (e.g. CI). Node-local only — no hub-push. Retention is unbounded for now (prune is a documented follow-up).

**Identity:** `PreflightRun.instance_id` is written with `local_instance_id()` (`apps/alerts/identity.py`), not raw `settings.INSTANCE_ID`, so it matches the key every `Node` row is registered under. A hub that never set the env var has an empty `INSTANCE_ID` and a hostname-fallback `Node`, so the raw value filed runs the node page could never find. Rows written before that was true are corrected by migration `0003_backfill_preflight_instance_id`, which carries a frozen copy of the identity rule. `get_profile()` (`apps/checkers/preflight/dashboard.py`) reports the same id, so what a run prints and what it is filed under agree.

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
- **Validate constructor inputs by type:**
  - **Full URLs / `base_url`** (`http://...`, `https://...`) → `validate_safe_url(url, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)` from `config.security`.
  - **Bare hostnames / IPs** (e.g. `NetworkChecker.hosts = ["8.8.8.8", "1.1.1.1"]`) → `ipaddress.ip_address(value)` for numeric IPs, or a hostname regex for DNS names. `validate_safe_url` will reject these because it requires a scheme.
  - **Filesystem paths** → `resolve_safe_path(path)` from `config.security`.
  - Fail closed on invalid input.
- **Class-level `scan_targets` / `LOG_DIRECTORIES` constants are intentionally not kwargs.** If you need an admin to customise targets, route through the `IntelligenceProvider`/`CHECKER_CONFIG` DB layer or Django settings — never accept these as caller-supplied kwargs that would flow through `provider_config` (see [Finding 1](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md) for the `scan_paths` precedent).
- **External API integrations** (StatusCake, PagerDuty, etc.) MUST use `safe_urlopen` from `config.security.http`; raw `urllib.request` is banned by ruff `TID251`.
- **Timeouts on every outbound call.** No bare `urlopen(req)` without `timeout=`.

### Trust boundary discipline
- Pipeline-mode checker inputs (`hostname`, `checker_configs`, `labels`) no longer arrive over HTTP: `POST /orchestration/pipeline/*` is a producer (ingest, then one run per changed incident) and forwards no checker configuration. CHECK still reads `checker_configs`/`labels` off a run's stored payload, so any future producer that writes them there is supplying untrusted input — treat it as such even after API-key auth.
- Standalone CLI inputs (`run_check --paths`) are admin-trusted but still routed through `resolve_safe_path` for defence in depth.
- Never echo raw exception messages into HTTP responses; log via `logger.exception(..., extra={"trace_id": ...})`.

### Audit checks before merging
- [ ] No new `subprocess` call without list-form argv and an explicit `timeout=`.
- [ ] Any new constructor-accepted path/host validated through `config.security`.
- [ ] No new path-bearing kwarg added that flows from `provider_config` without being added to `apps.intelligence.providers.BLOCKED_CONFIG_KEYS`.
- [ ] Run `uv run pytest apps/checkers/_tests/` and confirm scope-narrowing tests still pass.
