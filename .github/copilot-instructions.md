# GitHub Copilot Instructions

This file provides guidance to GitHub Copilot when working with code in this repository.

## Project Overview

Django-based server monitoring and alerting system with a strict 4-stage orchestration pipeline: `alerts → checkers → intelligence → notify`. The orchestrator controls all stage transitions—stages never call downstream stages directly.

## Pipeline Flow

```
alerts.ingest() → checkers.run() → intelligence.analyze() → notify.dispatch()
```

Each stage emits monitoring signals (`pipeline.stage.started/succeeded/failed`) with correlation IDs (`trace_id`, `run_id`).

### Core Rule: One Orchestrator, One Trace

- Only `apps.orchestration` may advance work between stages. Stage code must never call the next stage directly.
- Every pipeline run propagates `trace_id`/`run_id` across logs, monitoring, DB records, and outbound notifications.

## Core Apps

| App | Stage | Purpose | Key Models |
|-----|-------|---------|------------|
| `alerts` | ingest | Webhook ingestion (8 drivers) | Alert, Incident, AlertHistory |
| `checkers` | diagnose | Health checks (CPU, memory, disk, disk_macos, disk_linux, disk_common, network, process) | CheckRun |
| `intelligence` | analyze | AI analysis via provider pattern | AnalysisRun, uses StageExecution |
| `notify` | communicate | Notification delivery (Email, Slack, PagerDuty, Generic) | NotificationChannel |
| `orchestration` | controller | Pipeline state machine, retry logic | PipelineRun, StageExecution, PipelineDefinition |

## Architecture & Patterns

- **Driver/Provider Pattern**: All integrations inherit from abstract base classes (`BaseDriver`, `BaseChecker`, `BaseProvider`). New checkers/drivers/providers must follow this pattern.
- **DTOs**: Normalized data objects between stages (`ParsedPayload`, `CheckResult`, `AnalysisResult`).
- **Correlation IDs**: Every pipeline run has `trace_id` and `run_id` for end-to-end tracing.
- **Skip Controls**: `CHECKERS_SKIP_ALL=1` or `CHECKERS_SKIP=cpu,memory` to disable stages.
- **Idempotency**: Use idempotency keys for outbound notifications to prevent duplicates.

## App Structure (Required Layout)

All apps under `apps/` follow this layout:

```
apps/<app_name>/
├── views/          # Package (not monolithic views.py), organized by endpoint
├── _tests/         # Package mirroring source structure
├── agents.md       # App-specific AI/agent guidance
├── admin.py        # Extensive admin for operations
├── models.py
├── services.py     # Business logic
└── drivers/ or providers/ or checkers/  # Integration implementations
```

- **views/**: Must be a package directory, one module per endpoint (e.g., `views/webhook.py`, `views/health.py`).
- **_tests/**: Must mirror the source tree (e.g., `drivers/grafana.py` → `_tests/drivers/test_grafana.py`).
- **admin.py**: Every app must provide an extensive admin with filters, search, list displays, and pipeline tracing links.

## Code Conventions

- **Imports**: Always absolute — `from apps.alerts.models import Incident`
- **Line length**: 100 characters (Black + Ruff)
- **Formatting**: Black
- **Linting/imports**: Ruff
- **Testing**: pytest + pytest-django
- **Test coverage**: 100% branch coverage required for every PR. Run `uv run coverage run -m pytest && uv run coverage report` to verify.
- **Package manager**: uv (not pip)
- **Settings**: `config/settings.py`
- **Env vars**: `.env` (copy from `.env.sample`)

## Essential Commands

```bash
# Install dependencies
uv sync --extra dev

# Run tests
uv run pytest                              # All tests
uv run pytest apps/checkers/_tests/ -v     # Single app tests
uv run pytest apps/checkers/_tests/checkers/test_cpu.py -v  # Single file

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

# Pipeline testing
uv run python manage.py run_pipeline --sample
uv run python manage.py run_pipeline --sample --dry-run
```

## Stage Contracts

### alerts (ingest)

- Accept, validate, and parse inbound alert webhooks via drivers
- Output: `{ incident_id, alert_fingerprint, severity, source, normalized_payload_ref }`
- Never log/store secrets from inbound payloads

### checkers (diagnose)

- Run health checks for an incident (pipeline mode) or standalone
- Output: `{ checks: [...], timings, errors, checker_output_ref }`
- May call external APIs as diagnostic inputs (with timeouts/retries), but must not create incidents or notify

### intelligence (analyze)

- Produce analysis + recommendations using AI providers
- Output: `{ summary, probable_cause, actions, confidence, ai_output_ref, model_info }`
- Store redacted refs, never raw prompts/secrets

### notify (communicate)

- Render and dispatch notifications through configured channels
- Output: `{ deliveries: [...], provider_ids, notify_output_ref }`
- Use idempotency keys, set timeouts, never log tokens/webhook URLs

## Monitoring Requirements

Every stage must emit:
- `pipeline.stage.started`, `pipeline.stage.succeeded`, `pipeline.stage.failed` (with `retryable=true/false`)
- Duration metrics, retry/failure counters

Required tags: `trace_id/run_id`, `incident_id`, `stage`, `source`, `alert_fingerprint`, `environment`, `attempt`

## Failure & Retry Policy

- The orchestrator decides retryability
- Prefer stage-local retries with backoff for transient I/O
- If intelligence fails, pipeline may notify with "no AI analysis" fallback (must record the downgrade)
- Use idempotency keys for outbound notify

## Security Rules

- Never log secrets; store payloads/prompts as redacted refs
- Always set timeouts for external I/O
- Handle retries/backoff for outbound calls
- Input validation for all external payloads
- Redact secrets in admin displays (show refs, not values)

## Definition of Done

- Code follows existing base class/module patterns
- Config changes wired correctly (settings/env)
- Tests exist in `_tests/` mirroring source structure
- Admin updated if models changed
- Docs updated if behavior/config changed

## Key Documentation

- `CLAUDE.md` — Essential commands and architecture overview for Claude Code
- `agents.md` — AI agent roles, pipeline contracts, and conventions
- `docs/Architecture.md` — System architecture, all entry points, pipeline stages, data models
- `apps/<app>/README.md` — App-specific documentation
- App-level AI guidance (stage-specific contracts):
  - `apps/alerts/agents.md`
  - `apps/checkers/agents.md`
  - `apps/intelligence/agents.md`
  - `apps/notify/agents.md`
  - `apps/orchestration/agents.md`

## Quick Tips

- **Before coding**: Review the relevant app's `agents.md` for stage-specific contracts
- **New integrations**: Follow the driver/provider/checker pattern in the respective app
- **Testing**: Mirror the source structure in `_tests/` (e.g., `views/webhook.py` → `_tests/views/test_webhook.py`)
- **Pipeline work**: Always respect the orchestrator boundary—stages never call downstream stages
- **External I/O**: Always use timeouts, handle retries, and redact secrets
- **Admin**: Update admin.py with filters and search for new models
