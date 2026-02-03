# Sample Pipeline Definitions

This directory contains sample pipeline definition files for use with `run_pipeline --config`.

## Available Pipelines

### pipeline-manager.json
**Use case:** Central pipeline manager receiving alerts from multiple external servers.

Flow: `ingest → intelligence → notify` (skips checkers)

This pipeline:
- Receives alerts via webhook from external monitoring systems
- Analyzes alerts using AI (OpenAI/local provider)
- Sends notifications to configured channels
- Does NOT run local health checks (checkers are skipped)

Usage:
```bash
uv run python manage.py run_pipeline --config bin/pipelines/pipeline-manager.json --file alert.json
```

### local-monitor.json
**Use case:** Full monitoring pipeline for the local server.

Flow: `ingest → context → intelligence → notify`

This pipeline:
- Ingests alerts (can be from local `check_and_alert` or external webhook)
- Gathers local system context (CPU, memory, disk)
- Analyzes with AI including local metrics
- Sends notifications

Usage:
```bash
uv run python manage.py run_pipeline --config bin/pipelines/local-monitor.json --sample
```

## Creating Custom Pipelines

Copy one of the samples and modify the `nodes` array. Each node requires:
- `id`: Unique identifier
- `type`: One of `ingest`, `context`, `intelligence`, `notify`, `transform`
- `config`: Type-specific configuration
- `next`: (Optional) Next node ID

See `apps/orchestration/nodes/` for available node types and their configs.
