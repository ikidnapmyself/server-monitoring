# Real Node Handlers for Definition-Based Pipeline

**Date:** 2026-02-22
**Status:** Approved

## Overview

Replace the stub `ContextNodeHandler` and `NotifyNodeHandler` with real implementations
that call actual system checkers and notification drivers. This makes any `PipelineDefinition`
(including `local-monitor` from `setup_instance`) fully executable.

## Scope

Two node handlers need real implementations:

| Node | Current State | Target State |
|------|--------------|--------------|
| `context` | Returns fake data `{"cpu": {"load": 0.5}}` | Runs real checkers via `CHECKER_REGISTRY` |
| `notify` | Returns `{"delivered": True}` without sending | Sends via `NotifySelector` + `driver.send()` |

Two nodes are already functional and unchanged: `ingest`, `intelligence`.

## Context Node Handler

**Config:** `{"checker_names": ["cpu", "memory", "disk"]}` (optional)

If `checker_names` is missing/empty, use `get_enabled_checkers()` which respects `CHECKERS_SKIP`.

**Execution flow:**
1. Resolve checker names from config or `get_enabled_checkers()`
2. For each checker: instantiate class, call `.check()`, catch exceptions
3. Count passed (OK) vs failed (WARNING/CRITICAL/UNKNOWN)
4. Return all results

**Output:**
```python
{
    "checks_run": 3,
    "checks_passed": 2,
    "checks_failed": 1,
    "results": {
        "cpu": {"status": "ok", "message": "CPU usage: 12%", "metrics": {"usage_percent": 12.0}},
        "memory": {"status": "warning", "message": "...", "metrics": {...}},
    }
}
```

Errors from individual checkers don't fail the node — they're recorded in results with
status `"unknown"` and an error message. The node only adds to `result.errors` if zero
checkers could run.

**validate_config:** No required fields (empty config = run all enabled checkers).

## Notify Node Handler

**Config:** `{"drivers": ["slack", "email"]}` (list of driver types to send to)

Queries `NotificationChannel.objects.filter(driver__in=drivers, is_active=True)` to find
channels created by `setup_instance` or manually.

**Execution flow:**
1. Get `drivers` list from config
2. Query active `NotificationChannel` records matching those driver types
3. Build a `NotificationMessage` from previous node outputs (title, message body, severity)
4. For each channel: instantiate driver via `DRIVER_REGISTRY`, call `validate_config()`,
   call `driver.send(message, channel.config)`
5. Track attempted/succeeded/failed counts

**Message building:**
- Title: derived from checker results or "Pipeline Notification"
- Body: markdown summary of all `previous_outputs` (checker results, intelligence, etc.)
- Severity: highest severity from checker results, or "info" if no checkers ran

**Output:**
```python
{
    "channels_attempted": 2,
    "channels_succeeded": 1,
    "channels_failed": 1,
    "deliveries": [
        {"driver": "slack", "channel": "ops-slack", "status": "success", "message_id": "..."},
        {"driver": "email", "channel": "ops-email", "status": "failed", "error": "..."},
    ]
}
```

**Fallback:** If no matching `NotificationChannel` records exist, fall back to
`NotifySelector.resolve(None)` which picks the first active channel.

**validate_config:** Requires `drivers` (list) with at least one entry, OR a single
`driver` string (backwards compat with existing tests/configs).

## Config Compatibility

`setup_instance` writes `{"drivers": ["slack"]}` (plural). The old stub expected
`{"driver": "slack"}` (singular). The new handler accepts both:
- `drivers` (list) — preferred
- `driver` (string) — normalized to `[driver]`

## Testing

- Mock `CHECKER_REGISTRY` to use fake checkers for context node tests
- Mock `NotificationChannel.objects` and driver `.send()` for notify node tests
- Integration: run `run_pipeline --definition local-monitor` end-to-end with mocked drivers

## Files

```
apps/orchestration/nodes/context.py    # Replace stub
apps/orchestration/nodes/notify.py     # Replace stub
apps/orchestration/nodes/_tests/       # Tests (new directory or existing)
```
