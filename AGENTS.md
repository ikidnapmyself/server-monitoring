# Project Agent Guide

This file is the **canonical, tool-agnostic** source of guidance for AI agents working in this repository. Tool-specific entry files (`CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`) import or reference this file so there is exactly one place to edit project-wide rules.

## Documentation map

- **`AGENTS.md`** (this file) — repo-wide guidance: commands, architecture, conventions, agent roles, pipeline contracts.
- **`CLAUDE.md`** — Claude Code entry shim: `@`-imports this file and adds the Claude Code Skills (Superpowers) section.
- **`GEMINI.md`** — Gemini CLI entry shim: `@`-imports this file.
- **`.github/copilot-instructions.md`** — GitHub Copilot guidance; points readers back here for the authoritative version.
- **App-local rules** — each app's `AGENTS.md` carries stage-specific contracts, module notes, and admin conventions:
  - `apps/alerts/AGENTS.md`
  - `apps/checkers/AGENTS.md`
  - `apps/intelligence/AGENTS.md`
  - `apps/notify/AGENTS.md`
  - `apps/orchestration/AGENTS.md`
  - `bin/AGENTS.md`
- **`docs/`** — long-form docs served via GitHub Pages (`Architecture.md`, `Security.md`, `Installation.md`, plan documents under `plans/`).

---

## Project overview

Django-based server monitoring and alerting system. Producers write alert truth; alerts roll into incidents; the orchestrator runs a pipeline for each incident that materially changed, through an ordered subset of three stages: `diagnose (check) → analyze → notify`. The orchestrator controls all stage transitions; stages never call downstream stages directly.

---

## Essential commands

```bash
# Install dependencies
uv sync --extra dev

# Run tests
uv run pytest                                              # All tests
uv run pytest apps/checkers/_tests/                        # Single app
uv run pytest apps/checkers/_tests/checkers/test_cpu.py -v # Single file

# Code quality
uv run black .                             # Format
uv run ruff check . --fix                  # Lint + fix imports
uv run mypy .                              # Type check (optional)

# Pre-commit hooks
uv run pre-commit install
uv run pre-commit run --all-files

# Django
uv run python manage.py migrate
uv run python manage.py runserver
uv run python manage.py check              # Django system checks

# Health checks
uv run python manage.py check_health       # Run all checks
uv run python manage.py check_health --list
uv run python manage.py run_check cpu      # Single checker

# System preflight
uv run python manage.py preflight          # Dashboard + all checks
uv run python manage.py preflight --json   # JSON output for CI

# Pipeline testing
uv run python manage.py run_pipeline --sample
uv run python manage.py run_pipeline --sample --dry-run

# Security
uv run pip-audit --strict --desc           # Dependency CVE scan
uv run bandit -r apps/ config/ -c pyproject.toml
```

---

## Architecture

### Pipeline flow

```
produce truth  →  roll into incidents  →  enqueue one run per changed incident  →  drain
```

**Producing an alert is not a pipeline stage.** Six producers write alerts and let incidents
form, then hand the materially changed incidents to orchestration through the one shared
door, `enqueue_for` (`apps/orchestration/intake.py`):

| Producer | Where |
|---|---|
| the alerts webhook | `apps/alerts/views.py` |
| the `/orchestration/pipeline/` endpoint | `apps/orchestration/views.py` |
| `manage.py push_to_hub --local` | `apps/alerts/management/commands/push_to_hub.py` |
| `manage.py check_health` | `apps/checkers/management/commands/check_health.py` |
| `manage.py run_pipeline` | `apps/orchestration/management/commands/run_pipeline.py` |
| operator transitions | `IncidentManager` (`apps/alerts/`) |

None of them orchestrates. Each writes alerts, lets incidents form, and enqueues one
`PENDING` `PipelineRun` per materially changed incident, carrying the payload
`{"downstream_incident_id": N}`. **The incident is the subject of the run.**

**Draining is a mode, not a second path.** `enqueue_for(..., sync=True)` drains those runs
before returning — that is how `check_health` and `run_pipeline` finish the work for an
operator with no daemon. Without it the rows wait for `manage.py process_inbox`. Same rows,
same lanes, same executors either way; `sync` only decides who executes them and when.

**The stages are diagnose, analyze, notify.** A run executes an ordered subset of
`["check", "analyze", "notify"]`, chosen by the matched `PipelineDefinition`. `INGEST` is
retired as a stage: the enum value survives only so historical `StageExecution` rows still
render, and `apps/alerts/diagnosis.py` reports it only for incidents that have a legacy run.

Each stage emits monitoring signals (`pipeline.stage.started`, `pipeline.stage.succeeded`, `pipeline.stage.failed`) tagged with correlation IDs (`trace_id`, `run_id`).

**Core rule: one orchestrator, one trace.** Only the orchestrator (`apps.orchestration`) is allowed to move work from one stage to the next. Every pipeline run gets a correlation ID that must be attached to logs, monitoring events/spans, DB records / audit trail, and outbound notifications. Given a notification, you can jump back to the exact incident + checker output + analysis + retries/errors.

**Hard boundary rule:** stage code may call internal helpers in its own app, but must not call the next app directly. Only the orchestrator advances the pipeline.

**CHECK takes the incident as its subject.** For an incident run, the diagnose stage derives
its checkers from the distinct `checker` labels on that incident's alerts, filtered to the
local registry. An incident that names no checkers runs none — it no longer sweeps the whole
registry. That is why `CheckAlertBridge.run_checks_and_alert` distinguishes
`checker_names=None` ("every checker") from `checker_names=[]` ("none"); see
`apps/orchestration/AGENTS.md`.

**Checkers as an alert producer.** `apps.checkers` supplies the diagnose stage, but its
results are also a **source of alerts about this machine**, converted by `CheckAlertBridge`
(`apps/alerts/check_integration.py`) into ordinary `Alert`/`Incident` rows on two paths:

- **Inline** — `manage.py check_health` is the local entrypoint. It records alerts by default
  (`--no-alert` opts out), enqueues the runs their incidents earn and drains them before
  returning, so a single machine with no hub, no cron and nobody draining an inbox still gets
  alerts, incidents, analysis and notifications. `--no-notify` looks at a machine without
  paging anyone.
- **Queued** — `manage.py push_to_hub` POSTs the same results to a hub's webhook, and
  `push_to_hub --local` writes the alerts here instead of POSTing, enqueueing the same
  `PENDING` runs for `process_inbox` to drain.

**This does not change the stage model.** Producing an alert is not a stage transition:
the orchestrator still owns every transition, and neither path lets `apps.checkers` call a
downstream app. Both producers share one identity — fingerprint
`check:{instance_id}:{checker_name}` under `source: cluster`, with a stable alert name —
so one condition on one machine is one row however it arrived. See
`apps/alerts/AGENTS.md` → "Checker-alert identity".

**Diagnostic I/O clarification:** stages may call external systems (HTTP APIs, monitoring vendors) when needed to produce their own stage output — e.g. `apps.checkers` fetching StatusCake/uptime data or recent PagerDuty incident history. These calls must be treated as **inputs only** (no cross-stage advancement, no direct notifications). Always enforce timeouts/retries and redact secrets.

### App structure

Apps under `apps/` should follow this layout. A few legacy `views.py` modules — `apps/alerts/views.py`, `apps/notify/views.py`, `apps/orchestration/views.py` — are pending migration to the `views/` package form; all *new* apps must use the package layout from day one.

- `views/` — a package (not a monolithic `views.py`), organized by endpoint (e.g. `views/webhook.py`, `views/health.py`).
- `_tests/` — a package mirroring the source structure (e.g. `_tests/views/test_webhook.py`).
- `AGENTS.md` — app-specific AI agent guidance.
- `admin.py` — extensive admin for operations.

### Core apps

| App | Purpose | Key Models |
|-----|---------|------------|
| `alerts` | Webhook ingestion (8 drivers), incident rollup, operator transitions. A **producer**, not a stage | Alert, Incident, AlertHistory |
| `checkers` | Health checks (CPU, memory, disk, disk_macos, disk_linux, disk_common, disk_inodes, network, process, raid, disk_temp, cpu_temp, io_strain, listening_ports). Also a **producer of alert truth** about the machine it runs on, via `CheckAlertBridge` — see "Checkers as an alert producer" below | CheckRun |
| `intelligence` | AI analysis via provider pattern | Uses StageExecution |
| `notify` | Notification delivery (Email, Slack, PagerDuty, Generic) | NotificationChannel |
| `orchestration` | Intake (`intake.enqueue_for`), inbox, pipeline state machine, retry logic | PipelineRun, StageExecution, PipelineDefinition |

### Key patterns

- **Driver/Provider Pattern** — all integrations inherit from abstract base classes (e.g. `BaseDriver`, `BaseChecker`, `BaseProvider`).
- **DTOs** — normalized data objects between stages (`ParsedPayload`, `CheckResult`, `AnalysisResult`).
- **Correlation IDs** — every pipeline run has `trace_id` and `run_id` for tracing.
- **Stage configuration** — `PipelineDefinition` rows are the routing table: `match` conditions select a lane, its ordered `stages` list (a subset of `["check", "analyze", "notify"]`) selects which downstream stages run, and its single `channel` FK is the notify target. Unmatched traffic fails non-retryably as `no_route` — there is no implicit fallback, only the seeded `catch-all` row. `NotificationChannel.is_active` and `IntelligenceProvider.is_active` for DB-level enable/disable.

### Where stage-specific contracts live

- producing alert truth (drivers, incident rollup, operator transitions): `apps/alerts/AGENTS.md`
- diagnose: `apps/checkers/AGENTS.md`
- analyze: `apps/intelligence/AGENTS.md`
- communicate: `apps/notify/AGENTS.md`
- orchestration rules / state machine / node handlers: `apps/orchestration/AGENTS.md`

---

## Agent roles

Use the smallest agent that can complete the job safely and correctly.

- **Plan** — architecture, approach, multi-step work breakdown.
- **Coder** — implement code changes in specific files/directories.
- **Debug** — diagnose and fix failing tests/errors/logs.
- **Review** — quality, security, correctness, style, performance, edge cases.
- **Docs** — update docs/READMEs/usage guides for implemented changes.

For non-trivial changes, start with **Plan**, then hand the plan to **Coder**, then use **Review** and **Debug** as needed.

### Plan agent

**Purpose:** research and outline multi-step plans for complex monitoring workflows and architectural changes.

**When to use:**

- **Adding drivers:** designing new inbound alert drivers (e.g. adding Grafana webhooks to `apps/alerts/drivers/`).
- **New checkers:** architecting new system checkers (e.g. adding a Kubernetes pod status checker to `apps/checkers/`).
- **Intelligence:** planning LLM prompt strategies for incident analysis in `apps/intelligence/`.
- **Communication:** adding new notification drivers (e.g. PagerDuty or MS Teams) to `apps/notify/drivers/`.

**Plan deliverable (handoff contract):**

1. Files to add/change (paths and brief purpose)
2. Public interfaces (classes/functions, method signatures)
3. Config/settings/env vars (and defaults)
4. Error handling + edge cases
5. Tests to add/update
6. Acceptance criteria (what "done" means)

### Coder agent

**Purpose:** implement specific logic and code changes, following the project's conventions.

**When to use:**

- Implementing a driver/checker/provider described in a plan
- Refactoring a module or adding a small feature with clear scope
- Adding tests and wiring configuration

**Coder deliverable:**

- Code changes in the specified folders/files
- Minimal, well-scoped diffs
- Tests updated/added for the new behavior
- Notes on how to run/verify locally

### Debug agent

**Purpose:** troubleshoot errors, failing tests, runtime exceptions, incorrect behavior, and deployment issues.

**When to use:** failing CI/test output, stack traces, migrations failing, driver payload parsing issues, unexpected alerts / duplicated incidents / timeouts.

**Debug deliverable:** root cause explanation, minimal fix, regression test (when reasonable), verification steps.

### Review agent

**Purpose:** improve correctness, readability, security, performance, and consistency without changing intended behavior.

**When to use:** before merging a PR, after a large Coder change, when adding anything security-sensitive (webhooks, tokens, external APIs).

**Review checklist highlights:**

- Input validation for external payloads
- Idempotency for inbound alerts (avoid duplicate incidents)
- Timeouts/retries/backoff for outbound calls
- Avoid logging secrets and full payloads containing credentials
- Clear exception handling with actionable logs

### Docs agent

**Purpose:** keep documentation in sync with behavior and configuration. Used when adding new drivers/checkers/providers, new env vars or settings, or new management commands/runbooks.

---

## Pipeline-level rules

### Monitoring signals (every stage emits)

- `pipeline.stage.started`
- `pipeline.stage.succeeded`
- `pipeline.stage.failed` (with `retryable=true/false`)
- Duration metric (stage timing)
- Counters for retries and failures

**Minimum tags/fields on every signal:** `trace_id` / `run_id`, `incident_id`, `stage` (`check|analyze|notify`), `source` (grafana/alertmanager/custom), `alert_fingerprint`, `environment`, `attempt`.

**Artifacts to attach (or store refs to):**

- Normalised inbound payload ref, written by the producer (never raw secrets)
- Checker output ref
- Intelligence output ref (prompt/response refs, redacted)
- Notification delivery refs (provider message IDs, response codes)

Rule: never log secrets; payloads and prompts should be stored as **redacted refs** and only selectively attached.

### Failure & retry policy

- The orchestrator decides whether a failure is retryable.
- Prefer **stage-local retries** with backoff for transient I/O (HTTP timeouts, provider 5xx).
- Prefer **idempotency keys** for outbound notify to prevent duplicate messages.
- If `apps.intelligence` fails, the pipeline may still notify with a "no AI analysis available" fallback (configurable), but must record that downgrade in monitoring + audit trail.

### Mental model — producer, then orchestrator

A producer writes truth and enqueues; it never advances anything:

```
producer (webhook / check_health / push_to_hub --local / run_pipeline / operator transition):
  write alerts, let incidents form   → apps.alerts
  enqueue_for(result, sync=?)        → one PENDING run per materially changed incident
                                       payload {"downstream_incident_id": N}
  sync=True → drain them here; otherwise process_inbox drains them later
```

The orchestrator then runs each of those runs, and only it moves between stages:

```
start pipeline span (trace_id) for incident N
  resolve the lane      → PipelineDefinition.match picks the ordered stages + channel
  run checkers.run()    → record + emit signals   (checkers named by the incident's alerts)
  run intelligence.analyze() → record + emit signals
  run notify.dispatch() → record + emit signals
close pipeline span
```

A run whose payload is a legacy `{"driver", "payload"}` pair still drains, so pushes in
flight at deploy time are not lost. That branch is legacy-only and dies once no such rows
remain.

---

## Conventions and best practices

1. **Absolute imports always.** `from apps.alerts.models import Incident` — never relative.
2. **App layout is required.** Every app under `apps/<app_name>/` must include `views/` (package), `_tests/` (mirrors source layout), `AGENTS.md`, and a substantive `admin.py`.
3. **Django Admin is an operations surface.** Admin should make it easy to manage models and trace pipeline behavior via `Incident`, `trace_id` / `run_id`, and orchestration links. App-specific admin expectations live in each app's `AGENTS.md`. The custom `MonitoringAdminSite` (`config/admin.py`) drives the ops console: its dashboard (`config/dashboard.py` → `get_dashboard_context()` / `build_readiness()`, template `templates/admin/dashboard.html`) shows a **readiness panel** (channels / LLM provider / preflight / inbox / nodes, green-amber-red) plus activity cards. The nodes card counts **peers only** — this instance's own `Node` row (matched on `apps.alerts.identity.local_instance_id()`, never on `last_source`) is excluded from the freshness totals and surfaced as a self-check age, because a hub checked by hand is not a quiet agent; a peerless install reads `info` "Standalone", not amber. Its `get_app_list()` override regroups every registered model into operator-facing sections via `SECTION_MAP` (Operations / Configuration / History & Audit; unmapped models fall into "Other" — a completeness test guards the map). See `docs/plans/2026-08-11-admin-ops-console-design.md`.
4. **Driver / Provider pattern.** New checkers, drivers, and providers must inherit from the project's abstract base classes (`BaseDriver`, `BaseChecker`, `BaseProvider`, etc.).
5. **100% branch coverage on changed code.** Verify with `uv run coverage run -m pytest && uv run coverage report`.
6. **Line length: 100 characters** (Black + Ruff configured in `pyproject.toml`).
7. **Always use absolute paths.** Resolve all file/directory paths to absolute form using `pathlib.Path.resolve()` before use. Never pass user-supplied relative paths to file operations, subprocess calls, or provider methods. Validate that resolved paths fall within allowed directories to prevent path traversal.
8. **Always use full executable paths for subprocess.** Resolve via `shutil.which("toolname")` and pass the absolute result as `argv[0]` — never a bare name like `["less", "-FRX"]`. Bare-name PATH lookups at exec time let an attacker-controlled PATH steer the call. Pair with `# nosec B603  # nosemgrep` on the `subprocess.Popen` line so bandit and Semgrep's dynamic-argv detectors accept the (resolved) call as intentional.
9. **Be safe with external I/O.** Always set timeouts; handle retries; redact secrets from logs.
10. **Prefer small, testable units.** Parse and validate payloads separately from side effects (DB writes, network calls).
11. **Reference existing code.** Point agents to existing directories (e.g. `apps/checkers/`) so new code matches the established pattern.
12. **Package management via `uv`.**
    - Runtime deps: `uv add <package>`
    - Dev tooling: `uv sync --extra dev`
    - Django commands: `uv run python manage.py <command>`
13. **Environment variables.** Copy `.env.sample` to `.env` for local development. Main settings live in `config/settings.py`.

---

## Scope discipline — avoid over-build

These rules exist because the observability effort (#154/#155/#156, reverted) reinvented a node→hub mechanism the project already had. See the post-mortem: `docs/plans/2026-05-31-observability-overbuild-postmortem.md`.

1. **Inventory before building.** Before adding any new mechanism — endpoint, model, transport, management command — search the codebase for existing capability that already covers the need, and name it in the design. If it exists, reuse or extend it; do not build a parallel one. (The node→hub channel already existed: `push_to_hub` + the inbound `ClusterDriver` + the `/alerts/webhook/cluster/` path.)
2. **Respect the established pattern.** New integrations use the existing Driver/Provider base classes and the existing webhook ingestion path — not a parallel endpoint, auth scheme, or registry. If a design finds itself fighting the established pattern, stop and reconsider; that friction is a signal the approach is wrong.
3. **App vs. utility test.** Cross-cutting concerns (logging, correlation IDs, formatting) belong in shared configuration/utilities consumed through standard interfaces (e.g. stdlib `logging`), **not** in a Django app that other apps import. If an "app" ends up imported across the pipeline, it is a utility miscategorized as an app — that coupling is what turns a small feature into a 50-file revert.
4. **Solve the topology you have.** Do not build mesh, multi-hop, dedup, loop-prevention, or scaling machinery without a concrete, current requirement named in the design. Single-hop fan-in has no cycles to break and no duplicates to suppress. Relatedly, treat plan size as a smell, not rigor: an implementation plan that dwarfs the code it plans is a scope warning — re-scope before building.

---

## Tooling and CI

The repo standardises on:

- **Formatting:** Black (configured in `pyproject.toml`)
- **Linting / import sorting:** Ruff (configured in `pyproject.toml`)
- **Testing:** pytest + pytest-django (configured in `pyproject.toml`)
- **Optional typing:** mypy + django-stubs
- **Security:** pip-audit (deps), bandit (code)

CI runs these in GitHub Actions (`.github/workflows/ci.yml`). Any PR should keep the following green:

- `uv run black . --check`
- `uv run ruff check .`
- `uv run pytest`
- `uv run pip-audit --strict --desc`
- **100% branch coverage** on changed lines — `uv run coverage run -m pytest && uv run coverage report`

---

## Documentation and GitHub Pages

All markdown files under `docs/` are served via GitHub Pages (Jekyll + Just the Docs).

**Plan documents (`docs/plans/`)** require Jekyll front matter:

```yaml
---
title: "Plan Title Here"
parent: Plans
---
```

If a plan contains Jinja2/template syntax (`{% %}`, `{{ }}`), wrap the entire content (after the front matter) in `{% raw %}...{% endraw %}` to prevent Jekyll from interpreting it as Liquid tags. GitHub Pages uses Jekyll 3.x, which does not support `render_with_liquid: false`.

**Top-level docs under `docs/`** use title-case filenames (e.g. `Architecture.md`) and include:

```yaml
---
title: Page Title
layout: default
nav_order: N
---
```

**Historical record:** plan documents under `docs/plans/` are immutable historical records. Do not modify them to clean up stale references; they should describe the state of the world at the time they were written.

---

## Definition of Done

A change is typically "done" when:

- Code follows the existing base class / module patterns.
- Config changes are wired correctly (settings/env).
- Tests achieve 100% branch coverage on changed code.
- Basic verification steps are provided (how to run / check locally).
- Docs are updated if behavior or config changed.
- Security tooling is clean (`pip-audit`, `bandit`).
- All CI checks are green on the PR.

---

## Quick reference

| Agent | Use case | Example prompt |
|---|---|---|
| **Plan** | Multi-step planning & architecture | "Plan how to add a Disk Space checker to `apps/checkers/`" |
| **Coder** | Implementing specific logic | "Create the Slack notification driver in `apps/notify/drivers/slack.py`" |
| **Debug** | Troubleshooting errors | "Fix the circular import between `apps.alerts` and `apps.checkers`" |
| **Review** | Quality & security pass | "Review this webhook driver for validation & idempotency" |
| **Docs** | Documentation updates | "Document env vars + setup steps for the new driver" |