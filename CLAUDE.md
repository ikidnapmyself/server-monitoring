# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Skills (Superpowers)

Use these skills via `/skill-name` commands for disciplined workflows:

| Skill | When to Use |
|-------|-------------|
| `/brainstorming` | **Before any creative work** — new features, components, behavior changes |
| `/writing-plans` | When you have requirements for a multi-step task |
| `/executing-plans` | Execute a written plan in a separate session |
| `/test-driven-development` | Before writing implementation code |
| `/systematic-debugging` | When encountering bugs, test failures, unexpected behavior |
| `/verification-before-completion` | Before claiming work is done — run tests, confirm output |
| `/requesting-code-review` | After completing tasks or major features |
| `/receiving-code-review` | When receiving review feedback |
| `/using-git-worktrees` | For isolated feature work |
| `/finishing-a-development-branch` | When ready to merge/PR |
| `/dispatching-parallel-agents` | For 2+ independent tasks |
| `/subagent-driven-development` | Execute plans with independent tasks |

**Rule**: If there's even a 1% chance a skill applies, invoke it first.

## Project Overview

Django-based server monitoring and alerting system with a strict 4-stage orchestration pipeline: `alerts → checkers → intelligence → notify`. The orchestrator controls all stage transitions—stages never call downstream stages directly.

## Essential Commands

```bash
# Install dependencies
uv sync --extra dev

# Run tests
uv run pytest                              # All tests
uv run pytest apps/checkers/_tests/        # Single app
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

# System preflight
uv run python manage.py preflight          # Dashboard + all checks
uv run python manage.py preflight --json   # JSON output for CI

# Pipeline testing
uv run python manage.py run_pipeline --sample
uv run python manage.py run_pipeline --sample --dry-run
```

## Architecture

### Pipeline Flow
```
alerts.ingest() → checkers.run() → intelligence.analyze() → notify.dispatch()
```

Each stage emits monitoring signals (`pipeline.stage.started/succeeded/failed`) with correlation IDs (`trace_id`, `run_id`).

### App Structure

All apps under `apps/` follow this layout:
- `views/` — Package (not monolithic `views.py`), organized by endpoint
- `_tests/` — Package mirroring source structure (e.g., `_tests/views/test_webhook.py`)
- `agents.md` — App-specific AI agent guidance
- `admin.py` — Extensive admin for operations

### Core Apps

| App | Purpose | Key Models |
|-----|---------|------------|
| `alerts` | Webhook ingestion (8 drivers) | Alert, Incident, AlertHistory |
| `checkers` | Health checks (CPU, memory, disk, disk_macos, disk_linux, disk_common, network, process) | CheckRun |
| `intelligence` | AI analysis via provider pattern | Uses StageExecution |
| `notify` | Notification delivery (Email, Slack, PagerDuty, Generic) | NotificationChannel |
| `orchestration` | Pipeline state machine, retry logic | PipelineRun, StageExecution, PipelineDefinition |

### Key Patterns

- **Driver/Provider Pattern**: All integrations inherit from abstract base classes (e.g., `BaseDriver`, `BaseChecker`, `BaseProvider`)
- **DTOs**: Normalized data objects between stages (ParsedPayload, CheckResult, AnalysisResult)
- **Correlation IDs**: Every pipeline run has `trace_id` and `run_id` for tracing
- **Stage Configuration**: Pipeline definitions control which checkers/drivers/providers run; `NotificationChannel.is_active` and `IntelligenceProvider.is_active` for DB-level enable/disable

## Code Conventions

- **Imports**: Always absolute — `from apps.alerts.models import Incident`
- **Line length**: 100 characters (Black, Ruff)
- **Settings**: `config/settings.py`
- **Env vars**: Copy `.env.sample` to `.env`
- **Test coverage**: 100% branch coverage required for every PR. Run `uv run coverage run -m pytest && uv run coverage report` to verify.

## Documentation & GitHub Pages

- `docs/Architecture.md` — System architecture, all entry points, pipeline stages, data models
- `agents.md` — AI agent roles and pipeline contracts (read this for any significant work)
- `apps/<app>/README.md` — App-specific documentation
- `apps/<app>/agents.md` — App-specific AI guidance

### GitHub Pages Compliance

All markdown files under `docs/` are served via GitHub Pages (Jekyll + Just the Docs). When creating or modifying plan documents in `docs/plans/`, always include Jekyll front matter at the top:

```yaml
---
title: "Plan Title Here"
parent: Plans
---
```

If the plan contains Jinja2/template syntax (`{% %}`, `{{ }}`), wrap the entire content (after front matter) in `{% raw %}...{% endraw %}` to prevent Jekyll from interpreting it as Liquid tags. GitHub Pages uses Jekyll 3.x which does not support `render_with_liquid: false`.

For top-level docs under `docs/`, use title case filenames (e.g., `Architecture.md`) and include:

```yaml
---
title: Page Title
layout: default
nav_order: N
---
```
