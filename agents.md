# Agents

This document describes the available AI agents and how to use them efficiently in this modular server monitoring project.

## Documentation map (nested relationship)

- **Global rules (this file):** agent roles, pipeline-wide contracts, observability, and repo-wide conventions.
- **App-local rules:** see each app’s `agents.md` for stage-specific contracts, module notes, and conventions:
  - `apps/alerts/agents.md`
  - `apps/checkers/agents.md`
  - `apps/intelligence/agents.md`
  - `apps/notify/agents.md`
  - `apps/orchestration/agents.md`

---

## How to Choose an Agent

Use the smallest agent that can complete the job safely and correctly:

- **Plan** → Architecture, approach, and multi-step work breakdown.
- **Coder** → Implement code changes in specific files/directories.
- **Debug** → Diagnose and fix failing tests/errors/logs.
- **Review** → Improve quality: security, correctness, style, performance, edge cases.
- **Docs** → Update docs/READMEs/usage guides for implemented changes.

> Tip: For non-trivial changes, start with **Plan**, then hand the plan to **Coder**, then use **Review** and **Debug** as needed.

---

## The Orchestration Flow

This project is **pipeline-first**: a single orchestrator controls the full lifecycle of an incident through a strict, linear chain.

**Pipeline (always):** `apps.alerts` → `apps.checkers` → `apps.intelligence` → `apps.notify`

### Core rule: one orchestrator, one trace
- **Only the orchestrator** (`apps.orchestration`) is allowed to move work from one stage to the next.
- Every pipeline run gets a **correlation id** (e.g., `trace_id` / `run_id`) that must be attached to:
  - logs
  - monitoring events/spans
  - DB records / audit trail
  - outbound notifications

> Goal: given a notification, you can jump back to the exact incident + checker output + analysis + retries/errors.

### Where stage-specific contracts live

Stage responsibilities and DTO contracts are documented in the app-local files:
- ingest: `apps/alerts/agents.md`
- diagnose: `apps/checkers/agents.md`
- analyze: `apps/intelligence/agents.md`
- communicate: `apps/notify/agents.md`
- orchestration rules/state machine: `apps/orchestration/agents.md`

**Hard boundary rule:** stage code may call *internal helpers* in its own app, but must not call the next app directly. Only the orchestrator advances the pipeline.

**Diagnostic I/O clarification:** stages may call external systems (HTTP APIs, monitoring vendors) when needed to produce their own stage output.
- Example: `apps.checkers` fetching StatusCake/uptime data or recent PagerDuty incident history.
- These calls must be treated as **inputs only** (no cross-stage advancement, no direct notifications).
- Always enforce timeouts/retries and redact secrets.

---

### Monitoring tool requirements (track everything)

Introduce a single, app-agnostic monitoring surface (e.g. `apps.monitoring`) used by **every stage**.

**Minimum signals to emit per stage:**
- `pipeline.stage.started`
- `pipeline.stage.succeeded`
- `pipeline.stage.failed` (with `retryable=true/false`)
- duration metric (stage timing)
- counters for retries and failures

**Minimum tags/fields on every signal:**
- `trace_id/run_id`
- `incident_id`
- `stage` (`alerts|checkers|intelligence|notify`)
- `source` (grafana/alertmanager/custom)
- `alert_fingerprint`
- `environment`
- `attempt`

**Artifacts to attach (or store refs to):**
- normalized inbound payload ref (never raw secrets)
- checker output ref
- intelligence output ref (prompt/response refs, redacted)
- notification delivery refs (provider message IDs, response codes)

> Rule: never log secrets; payloads and prompts should be stored as **redacted refs** and only selectively attached.

---

### Failure & retry policy (pipeline-level)

- The orchestrator decides if a failure is retryable.
- Prefer **stage-local retries** with backoff for transient I/O (HTTP timeouts, provider 5xx).
- Prefer **idempotency keys** for outbound notify to prevent duplicate messages.
- If `apps.intelligence` fails, the pipeline may still notify with a “no AI analysis available” fallback (configurable), but must record that downgrade in monitoring + audit trail.

---

### A simple mental model

**Orchestrator pseudocode:**
- start pipeline span (trace_id)
- run `alerts.ingest()` → record + emit signals
- run `checkers.run()` → record + emit signals
- run `intelligence.analyze()` → record + emit signals
- run `notify.dispatch()` → record + emit signals
- close pipeline span

---

### Where agents help (within this flow)

- **Plan**: define stage contracts, DTOs, persistence, monitoring events, and failure policy.
- **Coder**: implement one stage + orchestrator wiring + monitoring calls.
- **Review**: verify boundary rule (no downstream calls), idempotency, timeouts/retries, secret redaction.
- **Debug**: trace a failed run end-to-end using `trace_id` and stage events.

---

## Available Agents

### Plan Agent

**Purpose:** Researches and outlines multi-step plans for complex monitoring workflows and architectural changes.

**When to use:**
- **Adding Drivers:** Designing new Inbound Alert Drivers (e.g., adding Grafana webhooks to `apps/alerts/drivers/`).
- **New Checkers:** Architecting new System Checkers (e.g., adding a Kubernetes pod status checker to `apps/checkers/`).
- **Intelligence:** Planning LLM prompt strategies for incident analysis in `apps/intelligence/`.
- **Communication:** Adding new Notification Drivers (e.g., PagerDuty or MS Teams) to `apps/notify/drivers/`.

**Plan deliverable (handoff contract):**
A good plan should include:
1. **Files to add/change** (paths and brief purpose)
2. **Public interfaces** (classes/functions, method signatures)
3. **Config/settings/env vars** (and defaults)
4. **Error handling + edge cases**
5. **Tests to add/update**
6. **Acceptance criteria** (what “done” means)

**Example prompts:**
- "Plan a new notification driver for Discord in `apps/notify/drivers/` following the existing BaseDriver pattern."
- "Plan the logic to parse a specific Prometheus AlertManager JSON payload in `apps/alerts/drivers/`."
- "Plan how to integrate a local Ollama instance as an alternative provider in `apps/intelligence/providers/`."

---

### Coder Agent

**Purpose:** Implements specific logic and code changes in the repo, following the project’s conventions.

**When to use:**
- Implementing a driver/checker/provider described in a plan
- Refactoring a module or adding a small feature with clear scope
- Adding tests and wiring configuration

**Coder deliverable:**
- Code changes in the specified folders/files
- Minimal, well-scoped diffs
- Tests updated/added for the new behavior
- Notes on how to run/verify locally

**Example prompts:**
- "Implement the Slack notification driver in `apps/notify/drivers/slack.py` using the BaseDriver pattern."
- "Add a Disk Space checker in `apps/checkers/` and include a unit test."
- "Wire a new `INTELLIGENCE_PROVIDER=ollama` option in `apps/intelligence/providers/` and update settings."

---

### Debug Agent

**Purpose:** Troubleshoots errors, failing tests, runtime exceptions, incorrect behavior, and deployment issues.

**When to use:**
- Failing CI/test output, stack traces, migrations failing
- Driver payload parsing issues
- Unexpected alerts, duplicated incidents, timeouts

**Debug deliverable:**
- Root cause explanation
- Minimal fix
- Regression test (when reasonable)
- Verification steps

**Example prompts:**
- "Fix this traceback when processing Grafana webhook alerts. Here are logs + payload."
- "Investigate why this checker is timing out and how to make it reliable."
- "Resolve the circular import between `apps.alerts` and `apps.checkers`."

---

### Review Agent

**Purpose:** Improves correctness, readability, security, performance, and consistency—without changing intended behavior.

**When to use:**
- Before merging a PR
- After a large Coder change
- When adding anything security-sensitive (webhooks, tokens, external APIs)

**Review checklist highlights:**
- Input validation for external payloads
- Idempotency for inbound alerts (avoid duplicate incidents)
- Timeouts/retries/backoff for outbound calls
- Avoid logging secrets and full payloads containing credentials
- Clear exception handling with actionable logs

**Example prompts:**
- "Review this new AlertManager driver for security and edge cases."
- "Suggest improvements for this checker’s reliability and test coverage."

---

### Docs Agent

**Purpose:** Keeps documentation in sync with behavior and configuration.

**When to use:**
- Adding new drivers/checkers/providers
- New env vars or settings
- New management commands or operational runbooks

**Example prompts:**
- "Document how to configure the new Discord driver (env vars + example payload)."
- "Update docs for new checker and how to run it locally."

---

## Best Practices (Project Rules)

1. **Respect the `apps/` Prefix:** All internal imports must start with `apps.` (e.g., `from apps.notify.base import BaseDriver`).
2. **Follow the Driver Pattern:** This project uses Abstract Base Classes (ABCs). New checkers/drivers/providers must inherit from the project’s base classes.
3. **Reference Existing Code:** Point agents to existing directories (e.g., `apps/checkers/`) so changes match the established pattern.
4. **App layout is consistent (required):** every app under `apps/<app_name>/` must include:
   - `views/` (a package directory), organized by endpoint/module (avoid a monolithic `views.py`)
     - Example: `apps/alerts/views/webhook.py`, `apps/alerts/views/health.py`
   - `tests/` (a package) that mirrors the directories/modules/classes being tested
     - Example: tests for `apps/alerts/views/webhook.py` live at `apps/alerts/tests/views/test_webhook.py`
     - Avoid piling everything into a single `tests.py` as the app grows
   - `agents.md` (app-local notes for prompts, conventions, and module-specific guidance)
5. **Django Admin is an operations surface (required):** every app must provide an **extensive** `admin.py`.
   - Admin should make it easy to manage the app’s models and trace pipeline behavior (via `Incident`, `trace_id/run_id`, and orchestration links).
   - App-specific admin expectations live in each app’s `agents.md`.
6. **Use uv for Packages:**
   - Runtime deps: `uv add <package>`
   - Dev tooling is installed via the `dev` extra: `uv sync --extra dev`
7. **Keep code formatted and lint-clean:**
   - Format with **Black**: `uv run black .`
   - Lint/sort imports with **Ruff**: `uv run ruff check . --fix`
8. **Prefer small, testable units:** Parse/validate payloads separately from side effects (DB writes, network calls).
9. **Be safe with external I/O:** Always set timeouts; handle retries; avoid leaking secrets in logs.

---

## Tooling & CI expectations

This repo standardizes on:

- **Formatting:** Black (configured in `pyproject.toml`)
- **Linting/import sorting:** Ruff (configured in `pyproject.toml`)
- **Testing:** pytest + pytest-django (configured in `pyproject.toml`)
- **Optional typing:** mypy + django-stubs

CI runs these checks in GitHub Actions (`.github/workflows/ci.yml`). Any PR should keep the following green:

- `uv run black . --check`
- `uv run ruff check .`
- `uv run pytest`

---

## Project-Specific Notes

- **Django Project Structure:** All custom apps live inside the `apps/` directory.
- **Imports:** Always use absolute imports: `from apps.alerts.models import Incident`.
- **Configuration:** Main settings are located in `config/settings.py`.
- **Package Management:** Managed by `uv`.
  - Django commands: `uv run python manage.py <command>`
  - Dev tools: `uv sync --extra dev`

---

## Definition of Done (for most changes)

A change is typically “done” when:
- Code follows the existing base class / module patterns
- Config changes are wired correctly (settings/env)
- Tests exist (or a clear reason why not)
- Basic verification steps are provided (how to run/check)
- Docs are updated if behavior/config changed

---

## Quick Reference

| Agent | Use Case | Example |
| :--- | :--- | :--- |
| **Plan** | Multi-step planning & architecture | "Plan how to add a Disk Space checker to `apps/checkers/`" |
| **Coder** | Implementing specific logic | "Create the Slack notification driver in `apps/notify/drivers/slack.py`" |
| **Debug** | Troubleshooting errors | "Fix the circular import between `apps.alerts` and `apps.checkers`" |
| **Review** | Quality & security pass | "Review this webhook driver for validation & idempotency" |
| **Docs** | Documentation updates | "Document env vars + setup steps for the new driver" |
