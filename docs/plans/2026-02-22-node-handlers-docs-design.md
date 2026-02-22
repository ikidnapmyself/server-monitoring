# Node Handlers Documentation Update — Design

> **Status:** Approved 2026-02-22

**Goal:** Update all project documentation to reflect the real ContextNodeHandler and NotifyNodeHandler implementations, add tutorial-style guides for building and running pipelines, and fix outdated references.

**Context:** The stub node handlers were replaced with real implementations that run actual system checkers and send real notifications via DB-configured channels. Documentation still has gaps, outdated references, and a config key mismatch in a sample pipeline.

## Files to Update

| File | Change Type | Scope |
|------|------------|-------|
| `apps/orchestration/README.md` | Major update | Node handler reference, output chaining, message building, tutorial, troubleshooting, annotated output |
| `apps/orchestration/management/commands/pipelines/README.md` | Addition | Sample vs Wizard comparison, per-sample annotations |
| `apps/orchestration/management/commands/pipelines/local-monitor.json` | Bug fix | `"include"` → `"checker_names"` config key |
| `docs/Architecture.md` | Update | Definition-based pipeline section with real node behavior |
| `apps/orchestration/agents.md` | Addition | Node handler contracts (input/output specs) |
| `apps/notify/README.md` | Fix | `NOTIFY_SKIP` example uses checker names instead of driver names |
| `docs/plans/2026-02-22-real-node-handlers-design.md` | Marker | Add completion status |

## Orchestration README — New Sections

### Node Handlers Reference
For each node type (ingest, context, intelligence, notify, transform):
- What it does (one sentence)
- Config options table (key, type, default, description)
- Output format (annotated JSON)
- Error behavior (graceful degradation vs hard fail)

### Node Output Chaining
How `NodeContext.previous_outputs` works — each node's output becomes available to downstream nodes by node ID. Data flow diagram through a real pipeline.

### Message Building Logic
How `NotifyNodeHandler._build_message()` constructs notifications:
- Severity derivation: critical > warning > info from checker results
- Title generation based on worst status
- Body assembly from checker results + intelligence summary
- Metadata attachment (trace_id, source, environment)

### Building a Custom Pipeline — Tutorial
Step-by-step: choose nodes → write JSON → validate with `--dry-run` → test with `--sample` → run for real.

### Annotated Sample Output
Real CLI output from `run_pipeline --definition` with annotations.

### Troubleshooting
Common issues: wrong config keys, missing channels, unknown drivers, partial failures.

### Fix
Update test path reference (`tests.py` → `_tests/`).

## Pipelines README — Sample vs Wizard

**Comparison section:**
- Samples = static JSON reference implementations for testing
- Wizard (`setup_instance`) = creates DB records with your actual drivers and credentials
- When to use which

**Per-sample annotations** explaining use case and differences.

## Architecture.md

Update definition-based pipeline section to describe real node behavior instead of just listing types.

## Orchestration agents.md

Add node handler contracts: what each node expects as input config and produces as output.

## Notify README

Fix `NOTIFY_SKIP=network,process` → valid driver names like `slack,email`.

## Design Doc Marker

Add `> **Status:** Completed 2026-02-22` to the real-node-handlers design doc.