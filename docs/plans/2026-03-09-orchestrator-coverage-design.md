---
title: "2026-03-09 Orchestrator Coverage Design"
parent: Plans
---

# Orchestrator Coverage Design

## Context

Both orchestrator files are core to the pipeline system but have coverage gaps:

- `definition_orchestrator.py` — 68% (validation edge cases, skip logic, error paths)
- `orchestrator.py` — 60% (resume, retry/backoff, fallback, error handlers)

## Approach

Sequential per-file: definition_orchestrator first (simpler), then orchestrator (retry/backoff needs careful mocking). Pure test additions — no source changes needed.

## definition_orchestrator.py (68% → 100%)

### Validation tests

| Test | Lines |
|------|-------|
| No nodes defined | 75 |
| Missing node id | 83 |
| Duplicate node id | 86 |
| Missing node type | 90 |
| Handler validation exception | 103-105 |
| Unknown next node reference | 113 |

### Execution tests

| Test | Lines |
|------|-------|
| `_should_skip` with skip_if_errors | 195-200 |
| `_should_skip` with skip_if_condition `.has_errors` | 362-370 |
| Ingest node propagates incident_id | 219-222 |
| Required node exception → pipeline failed | 240-251 |
| Outer exception handler | 265-274 |
| `_execute_node` exception re-raises with stage record | 328-334 |

## orchestrator.py (60% → 100%)

### Resume tests

| Test | Lines |
|------|-------|
| `resume_pipeline` run not found | 216-219 |
| `resume_pipeline` wrong status | 221-222 |
| `resume_pipeline` success | 224-225 |

### Execution tests

| Test | Lines |
|------|-------|
| Skip completed stages on resume | 274-284 |
| ANALYZE with fallback_used continues | 337-342 |
| Generic exception handler | 389-405 |

### Retry tests (mock `time.sleep`)

| Test | Lines |
|------|-------|
| StageExecutionError retry + backoff | 499-521 |
| Generic exception retry + backoff | 523-544 |
| Unreachable safety net | 547-550 |

## Target

100% branch coverage on both files.