# Agents

This document describes the available AI agents and how to use them efficiently in this modular server monitoring project.

## How to Choose an Agent

Use the smallest agent that can complete the job safely and correctly:

- **Plan** → Architecture, approach, and multi-step work breakdown.
- **Coder** → Implement code changes in specific files/directories.
- **Debug** → Diagnose and fix failing tests/errors/logs.
- **Review** → Improve quality: security, correctness, style, performance, edge cases.
- **Docs** → Update docs/READMEs/usage guides for implemented changes.

> Tip: For non-trivial changes, start with **Plan**, then hand the plan to **Coder**, then use **Review** and **Debug** as needed.

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
4. **Use uv for Packages:**
   - Runtime deps: `uv add <package>`
   - Dev tooling is installed via the `dev` extra: `uv sync --extra dev`
5. **Keep code formatted and lint-clean:**
   - Format with **Black**: `uv run black .`
   - Lint/sort imports with **Ruff**: `uv run ruff check . --fix`
6. **Prefer small, testable units:** Parse/validate payloads separately from side effects (DB writes, network calls).
7. **Be safe with external I/O:** Always set timeouts; handle retries; avoid leaking secrets in logs.

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
