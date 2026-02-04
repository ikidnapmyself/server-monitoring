# Sample Pipeline Definitions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add sample pipeline definition files to `apps/orchestration/management/commands/pipelines/` that demonstrate two usage modes: (1) Pipeline Manager mode (webhook → analyze → notify, skipping checkers) for managing alerts from multiple external servers, and (2) Default/Local mode (full 4-stage pipeline) for monitoring the local server.

**Architecture:** Create JSON pipeline definition files in `apps/orchestration/management/commands/pipelines/` that can be loaded via `run_pipeline --config <path>`. Each file contains a complete `PipelineDefinition` config with nodes array. The Pipeline Manager mode uses only `ingest → intelligence → notify` nodes (no context/checkers), while Default mode includes all stages.

**Tech Stack:** JSON files, existing `DefinitionBasedOrchestrator`, existing node handlers (ingest, intelligence, notify, context)

---

## Alert Context Flow

When alerts arrive from external sources (PagerDuty, Grafana, Alertmanager), the pipeline system passes alert context to the intelligence/analyze node through multiple channels:

### 1. Database Path (Primary - Full Context)
```
Alert Payload → Ingest Node → Creates Alert & Incident in DB
                                    ↓
                              Intelligence Node receives incident_id
                                    ↓
                              Fetches Incident from DB (includes all Alert.raw_payload)
                                    ↓
                              Passes full Incident to provider.analyze()
```

The `Incident` object passed to intelligence providers contains:
- `title`, `status`, `severity`, `description`, `summary`
- Related `alerts` via foreign key, each with:
  - `raw_payload` - the original webhook payload from PagerDuty/Grafana
  - `labels`, `annotations`, `source`, `name`

### 2. Direct Context Path (Lightweight)
```
NodeContext {
  payload: {...}           // Original alert payload (raw)
  source: "pagerduty"      // Alert source identifier
  incident_id: 42          // Set by ingest node
  previous_outputs: {
    "ingest": {            // Ingest node output
      "severity": "critical",
      "alert_fingerprint": "abc123",
      "incident_id": 42
    }
  }
}
```

### Context Available to Intelligence Providers

| Source | Access | Contains |
|--------|--------|----------|
| Database | `Incident.objects.get(incident_id)` | Full incident history, all alerts, raw payloads |
| NodeContext.payload | `ctx.payload` | Original alert webhook payload |
| Previous outputs | `ctx.previous_outputs["ingest"]` | Severity, fingerprint, incident_id |
| Source identifier | `ctx.source` | "pagerduty", "grafana", "alertmanager" |

---

## Task 1: Create Pipeline Definitions Directory Structure

**Files:**
- Create: `apps/orchestration/management/commands/pipelines/` directory
- Create: `apps/orchestration/management/commands/pipelines/README.md`

**Step 1: Create the pipelines directory**

```bash
mkdir -p apps/orchestration/management/commands/pipelines
```

**Step 2: Create README documenting the pipeline definitions**

Create `apps/orchestration/management/commands/pipelines/README.md`:

```markdown
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
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --file alert.json
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
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --sample
```

## Creating Custom Pipelines

Copy one of the samples and modify the `nodes` array. Each node requires:
- `id`: Unique identifier
- `type`: One of `ingest`, `context`, `intelligence`, `notify`, `transform`
- `config`: Type-specific configuration
- `next`: (Optional) Next node ID

See `apps/orchestration/nodes/` for available node types and their configs.
```

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/README.md
git commit -m "docs: add pipelines directory with README for run_pipeline command"
```

---

## Task 2: Create Pipeline Manager Definition (No Checkers)

**Files:**
- Create: `apps/orchestration/management/commands/pipelines/pipeline-manager.json`
- Test: Manual validation with `--dry-run`

**Step 1: Create the pipeline-manager.json file**

Create `apps/orchestration/management/commands/pipelines/pipeline-manager.json`:

```json
{
  "version": "1.0",
  "description": "Pipeline manager for external alerts - skips local health checks. Use this when this server acts as a central pipeline controller receiving webhooks from multiple monitored servers.",
  "defaults": {
    "max_retries": 3,
    "timeout_seconds": 300
  },
  "nodes": [
    {
      "id": "ingest",
      "type": "ingest",
      "config": {},
      "next": "analyze"
    },
    {
      "id": "analyze",
      "type": "intelligence",
      "config": {
        "provider": "local"
      },
      "required": false,
      "next": "notify"
    },
    {
      "id": "notify",
      "type": "notify",
      "config": {
        "driver": "generic"
      }
    }
  ]
}
```

**Step 2: Verify the definition is valid with dry-run**

Run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --sample --dry-run
```

Expected: Output showing the pipeline definition would be executed with 3 nodes (ingest, analyze, notify)

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/pipeline-manager.json
git commit -m "feat: add pipeline-manager.json for external alert processing"
```

---

## Task 3: Create Local Monitor Definition (Full Pipeline)

**Files:**
- Create: `apps/orchestration/management/commands/pipelines/local-monitor.json`
- Test: Manual validation with `--dry-run`

**Step 1: Create the local-monitor.json file**

Create `apps/orchestration/management/commands/pipelines/local-monitor.json`:

```json
{
  "version": "1.0",
  "description": "Full local monitoring pipeline - ingests alerts, gathers system context, analyzes with AI, and notifies. Use this for monitoring the local server.",
  "defaults": {
    "max_retries": 3,
    "timeout_seconds": 300
  },
  "nodes": [
    {
      "id": "ingest",
      "type": "ingest",
      "config": {},
      "next": "context"
    },
    {
      "id": "context",
      "type": "context",
      "config": {
        "include": ["cpu", "memory", "disk"]
      },
      "next": "analyze"
    },
    {
      "id": "analyze",
      "type": "intelligence",
      "config": {
        "provider": "local"
      },
      "required": false,
      "next": "notify"
    },
    {
      "id": "notify",
      "type": "notify",
      "config": {
        "driver": "generic"
      }
    }
  ]
}
```

**Step 2: Verify the definition is valid with dry-run**

Run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --sample --dry-run
```

Expected: Output showing the pipeline definition would be executed with 4 nodes (ingest, context, analyze, notify)

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/local-monitor.json
git commit -m "feat: add local-monitor.json for full local server monitoring"
```

---

## Task 4: Create OpenAI Variant of Pipeline Manager

**Files:**
- Create: `apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json`
- Test: Manual validation with `--dry-run`

**Step 1: Create the pipeline-manager-openai.json file**

Create `apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json`:

```json
{
  "version": "1.0",
  "description": "Pipeline manager using OpenAI for analysis - requires OPENAI_API_KEY env var. Skips local health checks.",
  "defaults": {
    "max_retries": 3,
    "timeout_seconds": 300
  },
  "nodes": [
    {
      "id": "ingest",
      "type": "ingest",
      "config": {},
      "next": "analyze"
    },
    {
      "id": "analyze",
      "type": "intelligence",
      "config": {
        "provider": "openai",
        "provider_config": {
          "model": "gpt-4o-mini",
          "max_tokens": 1024
        }
      },
      "required": false,
      "next": "notify"
    },
    {
      "id": "notify",
      "type": "notify",
      "config": {
        "driver": "generic"
      }
    }
  ]
}
```

**Step 2: Verify the definition is valid with dry-run**

Run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json --sample --dry-run
```

Expected: Output showing the pipeline definition would be executed with 3 nodes

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json
git commit -m "feat: add pipeline-manager-openai.json with OpenAI provider"
```

---

## Task 5: Create PagerDuty Alert Pipeline with Context Enrichment

**Files:**
- Create: `apps/orchestration/management/commands/pipelines/pagerduty-alert.json`
- Test: Manual validation with `--dry-run`

**Purpose:** Demonstrate how external alert context (PagerDuty incident details) flows through the pipeline to the intelligence/analyze step. When PagerDuty sends a webhook, the alert payload includes rich context (incident description, severity, links, custom details) that should inform the AI analysis.

**Step 1: Create the pagerduty-alert.json file**

Create `apps/orchestration/management/commands/pipelines/pagerduty-alert.json`:

```json
{
  "version": "1.0",
  "description": "Pipeline for PagerDuty alerts - receives webhook events, passes full alert context to intelligence provider for analysis. Use for incident response automation.",
  "defaults": {
    "max_retries": 3,
    "timeout_seconds": 300
  },
  "context_flow": {
    "_comment": "Documents how PagerDuty alert context reaches the analyze step",
    "pagerduty_webhook": "Alert payload received at /webhooks/pagerduty/",
    "ingest_node": "Parses PagerDuty event, creates Incident & Alert in DB, stores raw_payload",
    "intelligence_node": "Fetches Incident from DB, provider.analyze() receives full incident with alert.raw_payload"
  },
  "nodes": [
    {
      "id": "ingest",
      "type": "ingest",
      "config": {
        "source_hint": "pagerduty"
      },
      "next": "analyze"
    },
    {
      "id": "analyze",
      "type": "intelligence",
      "config": {
        "provider": "openai",
        "provider_config": {
          "model": "gpt-4o-mini",
          "max_tokens": 2048,
          "system_prompt_hint": "Analyze the PagerDuty incident. The incident object includes related alerts with their original webhook payload in alert.raw_payload. Use this context to provide actionable recommendations."
        }
      },
      "required": false,
      "next": "notify"
    },
    {
      "id": "notify",
      "type": "notify",
      "config": {
        "driver": "pagerduty",
        "include_recommendations": true
      }
    }
  ]
}
```

**Step 2: Verify the definition is valid with dry-run**

Run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pagerduty-alert.json --sample --dry-run
```

Expected: Output showing the pipeline definition would be executed with 3 nodes

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/pagerduty-alert.json
git commit -m "feat: add pagerduty-alert.json with context enrichment documentation"
```

---

## Task 6: Add Tests for Pipeline Definition Loading

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`
- Test: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write tests for loading sample pipeline definitions**

Add to `apps/orchestration/tests/test_run_pipeline_command.py`:

```python
class TestSamplePipelineDefinitions:
    """Tests for apps/orchestration/management/commands/pipelines/ sample definition files."""

    def test_pipeline_manager_json_is_valid(self):
        """Verify pipeline-manager.json can be loaded and validated."""
        import json
        from pathlib import Path

        from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
        from apps.orchestration.models import PipelineDefinition

        config_path = Path("apps/orchestration/management/commands/pipelines/pipeline-manager.json")
        assert config_path.exists(), f"Missing {config_path}"

        with open(config_path) as f:
            config = json.load(f)

        definition = PipelineDefinition(name="test-pm", config=config)
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert errors == [], f"Validation errors: {errors}"
        assert len(definition.get_nodes()) == 3
        assert definition.get_nodes()[0]["type"] == "ingest"
        assert definition.get_nodes()[1]["type"] == "intelligence"
        assert definition.get_nodes()[2]["type"] == "notify"

    def test_local_monitor_json_is_valid(self):
        """Verify local-monitor.json can be loaded and validated."""
        import json
        from pathlib import Path

        from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
        from apps.orchestration.models import PipelineDefinition

        config_path = Path("apps/orchestration/management/commands/pipelines/local-monitor.json")
        assert config_path.exists(), f"Missing {config_path}"

        with open(config_path) as f:
            config = json.load(f)

        definition = PipelineDefinition(name="test-lm", config=config)
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert errors == [], f"Validation errors: {errors}"
        assert len(definition.get_nodes()) == 4
        node_types = [n["type"] for n in definition.get_nodes()]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

    def test_pipeline_manager_openai_json_is_valid(self):
        """Verify pipeline-manager-openai.json can be loaded and validated."""
        import json
        from pathlib import Path

        from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
        from apps.orchestration.models import PipelineDefinition

        config_path = Path("apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json")
        assert config_path.exists(), f"Missing {config_path}"

        with open(config_path) as f:
            config = json.load(f)

        definition = PipelineDefinition(name="test-pm-openai", config=config)
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert errors == [], f"Validation errors: {errors}"
        # Verify OpenAI provider config
        analyze_node = definition.get_nodes()[1]
        assert analyze_node["config"]["provider"] == "openai"
        assert analyze_node["config"]["provider_config"]["model"] == "gpt-4o-mini"

    def test_pagerduty_alert_json_is_valid(self):
        """Verify pagerduty-alert.json can be loaded and validated."""
        import json
        from pathlib import Path

        from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
        from apps.orchestration.models import PipelineDefinition

        config_path = Path("apps/orchestration/management/commands/pipelines/pagerduty-alert.json")
        assert config_path.exists(), f"Missing {config_path}"

        with open(config_path) as f:
            config = json.load(f)

        definition = PipelineDefinition(name="test-pd-alert", config=config)
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert errors == [], f"Validation errors: {errors}"
        assert len(definition.get_nodes()) == 3
        # Verify context_flow documentation exists
        assert "context_flow" in config
        # Verify ingest has source_hint
        ingest_node = definition.get_nodes()[0]
        assert ingest_node["config"].get("source_hint") == "pagerduty"
```

**Step 2: Run the tests**

Run:
```bash
uv run pytest apps/orchestration/tests/test_run_pipeline_command.py::TestSamplePipelineDefinitions -v
```

Expected: All 4 tests pass

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add validation tests for sample pipeline definitions"
```

---

## Task 7: Update apps/orchestration/management/commands/pipelines/README with Usage Examples and Context Flow

**Files:**
- Modify: `apps/orchestration/management/commands/pipelines/README.md`

**Step 1: Add concrete usage examples to README**

Update `apps/orchestration/management/commands/pipelines/README.md` to add example payloads section:

```markdown
## Example Alert Payloads

### Grafana Alert
```json
{
  "alertname": "HighCPU",
  "severity": "critical",
  "instance": "web-server-01",
  "description": "CPU usage above 90% for 5 minutes"
}
```

Save as `alert.json` and run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --file alert.json
```

### Alertmanager Alert
```json
{
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "DiskSpaceLow",
        "severity": "warning",
        "instance": "db-server-01"
      },
      "annotations": {
        "description": "Disk usage above 85%"
      }
    }
  ]
}
```

### PagerDuty Alert (Webhook)
```json
{
  "event": {
    "id": "01ABCDEF",
    "event_type": "incident.triggered",
    "resource_type": "incident",
    "occurred_at": "2024-01-15T10:30:00.000Z",
    "data": {
      "id": "P123ABC",
      "type": "incident",
      "title": "High CPU on web-server-01",
      "status": "triggered",
      "urgency": "high",
      "service": {
        "id": "PABCDEF",
        "name": "Production Web Servers"
      },
      "body": {
        "details": {
          "cpu_percent": 95,
          "process": "nginx",
          "duration_minutes": 10
        }
      }
    }
  }
}
```

Save as `pagerduty-alert.json` and run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pagerduty-alert.json --file pagerduty-alert.json
```

## Alert Context Flow to Intelligence Providers

When the pipeline processes an alert, context flows to the intelligence/analyze step:

1. **Ingest Node**: Parses the alert, creates `Incident` and `Alert` records in DB
   - Stores original payload in `Alert.raw_payload`
   - Returns `incident_id`, `severity`, `source`

2. **Intelligence Node**: Receives context via:
   - `ctx.incident_id` → Fetches full `Incident` from DB
   - `incident.alerts` → Access all related alerts with `raw_payload`
   - `ctx.payload` → Original webhook payload
   - `ctx.previous_outputs["ingest"]` → Ingest node output

3. **Provider receives**: Full `Incident` object with all alert details

For 3rd party intelligence providers (OpenAI, etc.), the provider's `analyze(incident)` method receives the complete incident, enabling context-aware analysis.

## Environment Variables

For OpenAI-based pipelines:
```bash
export OPENAI_API_KEY="your-api-key"
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json --file alert.json
```

## Notification Drivers

Change the `notify` node's `driver` config to use different notification backends:
- `generic` - HTTP webhook (default)
- `slack` - Slack webhook
- `email` - Email via SMTP
- `pagerduty` - PagerDuty Events API

Example with Slack:
```json
{
  "id": "notify",
  "type": "notify",
  "config": {
    "driver": "slack"
  }
}
```
```

**Step 2: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/README.md
git commit -m "docs: add usage examples and payload samples to pipelines README"
```

---

## Task 8: Final Integration Test

**Files:**
- Test: Manual end-to-end verification

**Step 1: Run dry-run for all pipeline definitions**

Run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --sample --dry-run
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --sample --dry-run
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json --sample --dry-run
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pagerduty-alert.json --sample --dry-run
```

Expected: All four show valid pipeline structure without errors

**Step 2: Run all tests to ensure nothing is broken**

Run:
```bash
uv run pytest
```

Expected: All tests pass

**Step 3: Verify files are properly formatted**

Run:
```bash
uv run black --check .
uv run ruff check .
```

Expected: No formatting or linting errors

**Step 4: Final commit for any cleanup**

If any formatting fixes needed:
```bash
uv run black .
uv run ruff check . --fix
git add -A
git commit -m "chore: format and lint fixes"
```

---

## Summary

This plan creates:

1. **apps/orchestration/management/commands/pipelines/** - New directory for pipeline definitions
2. **pipeline-manager.json** - For central alert processing (ingest → analyze → notify)
3. **local-monitor.json** - For local server monitoring (ingest → context → analyze → notify)
4. **pipeline-manager-openai.json** - Pipeline manager with OpenAI provider
5. **pagerduty-alert.json** - PagerDuty webhook pipeline with context enrichment documentation
6. **README.md** - Documentation with usage examples and context flow
7. **Tests** - Validation tests ensuring definitions stay valid

### Pipeline Types

| Pipeline | Use Case | Nodes | Context Source |
|----------|----------|-------|----------------|
| pipeline-manager | Central alert receiver | ingest → analyze → notify | Alert payload via DB |
| local-monitor | Local server monitoring | ingest → context → analyze → notify | Local system metrics + alert |
| pipeline-manager-openai | External alerts with OpenAI | ingest → analyze → notify | Alert payload via DB |
| pagerduty-alert | PagerDuty incident response | ingest → analyze → notify | Full PagerDuty event in Alert.raw_payload |

### Alert Context Flow

```
External Alert (PagerDuty/Grafana/etc.)
        ↓
    Ingest Node
        ↓ creates Incident & Alert (stores raw_payload)
        ↓ returns incident_id
    Intelligence Node
        ↓ fetches Incident from DB
        ↓ provider.analyze(incident) with full context
    Notify Node
```

The intelligence provider receives the complete `Incident` object, which includes all related `Alert` records with their original `raw_payload`. This enables context-aware analysis for 3rd party intelligence providers like OpenAI.
