# Run Pipeline Definitions via CLI

## Overview

Extend `run_pipeline.py` to support definition-based pipelines, allowing execution of `PipelineDefinition` records or ad-hoc JSON config files from the command line.

## Command Interface

```bash
# Run a definition from the database
python manage.py run_pipeline --definition health-check --payload '{"server": "web-01"}'

# Run from a JSON config file
python manage.py run_pipeline --config ./pipelines/custom.json --sample

# Existing hardcoded pipeline still works
python manage.py run_pipeline --sample --source grafana
```

### New Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `--definition` | string | Name of a `PipelineDefinition` in the database |
| `--config` | path | Path to a JSON file containing pipeline config |

### Selection Logic

1. If `--definition` provided → load from database, use `DefinitionBasedOrchestrator`
2. Else if `--config` provided → load JSON file, use `DefinitionBasedOrchestrator`
3. Else → use existing `PipelineOrchestrator` (hardcoded)

### Constraints

- `--definition` and `--config` are mutually exclusive
- All existing payload flags (`--sample`, `--payload`, `--file`) work with both orchestrators

## Dry-Run Output

For definition-based pipelines:

```
=== DRY RUN ===

Pipeline Definition: health-check
Source: cli
Environment: development

Nodes (3):
  1. [context] metrics
     Config: {"include": ["cpu", "memory", "disk"]}
     → next: analyze

  2. [intelligence] analyze
     Config: {"provider": "openai"}
     → next: notify

  3. [notify] notify
     Config: {"driver": "slack"}
     → end

Payload:
{
  "server": "web-01"
}

Use without --dry-run to execute
```

For ad-hoc JSON configs, shows `"Pipeline Config: <filepath>"` instead of definition name.

## Result Display

```
============================================================
PIPELINE RESULT
============================================================

Status: COMPLETED
Definition: health-check
Trace ID: abc-123
Run ID: 42
Duration: 1523.45ms

--- context (metrics) ---
  Checks run: 3
  Duration: 234.12ms

--- intelligence (analyze) ---
  Summary: System healthy, minor CPU spike detected...
  Provider: openai
  Duration: 1105.67ms

--- notify (notify) ---
  Driver: slack
  Channels attempted: 1
  Succeeded: 1
  Duration: 183.66ms

✓ Pipeline completed successfully
```

The `--json` flag outputs raw `PipelineRun` result as JSON.

## Error Handling

### Validation Errors

```bash
# Definition not found
CommandError: Pipeline definition not found: nonexistent

# Invalid JSON file
CommandError: Invalid JSON in config file: ...

# Config file not found
CommandError: Config file not found: missing.json

# Both flags provided
CommandError: Cannot specify both --definition and --config
```

### Pipeline Validation

Before execution, call `orchestrator.validate()`. On failure:

```
CommandError: Pipeline definition invalid:
  - Node 'analyze' has unknown type: ai
  - Node 'notify' references non-existent next: 'missing'
```

### Runtime Errors

Wrapped as `CommandError: Pipeline failed: <message>`

## Implementation Notes

### Files to Modify

- `apps/orchestration/management/commands/run_pipeline.py` - Add new arguments and orchestrator selection logic

### Key Changes

1. Add `--definition` and `--config` arguments
2. Add `_get_definition()` method to load definition from database or file
3. Add `_show_definition_dry_run()` for definition-specific dry-run output
4. Add `_display_definition_result()` for definition-specific result display
5. Update `handle()` to select orchestrator based on flags
