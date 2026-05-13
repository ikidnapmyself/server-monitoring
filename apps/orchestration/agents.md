# apps.orchestration — Agent Notes

This file contains **app-local** guidance for working in `apps/orchestration/`.

## Role in the pipeline

This app is the **only pipeline controller**.

Core rule: **one orchestrator, one trace**
- Only `apps.orchestration` is allowed to move work across stages.
- Every run must propagate `trace_id/run_id` across logs, monitoring, DB records, and outbound notifications.

## Responsibilities

The orchestrator owns:
- State machine: `INGESTED → CHECKED → ANALYZED → NOTIFIED` (+ failure/retry states)
- Stage contract enforcement (structured DTOs)
- Persistence/audit trail (`PipelineRun`, `StageExecution`, output snapshots/refs)
- Observability (mandatory stage boundary signals)
- Failure & retry policy (including intelligence fallback when configured)

## Monitoring signals (minimum)

Emit, at least:
- `pipeline.stage.started`
- `pipeline.stage.succeeded`
- `pipeline.stage.failed` (with `retryable=true/false`)
- duration metric

Required tags/fields:
- `trace_id/run_id`, `incident_id`, `stage`, `source`, `alert_fingerprint`, `environment`, `attempt`

## Key modules

- `apps/orchestration/orchestrator.py` — pipeline implementation
- `apps/orchestration/tasks.py` — Celery task entrypoints
- `apps/orchestration/models.py` — `PipelineRun`, `StageExecution`
- `apps/orchestration/executors.py` / `dtos.py` — stage execution helpers and DTOs
- `apps/orchestration/urls.py` — URL routing

## Node handler contracts

Each definition-based pipeline node has a handler in `apps/orchestration/nodes/`. Below are the input/output contracts:

| Node Type | Config Required | Config Optional | Output Keys | Error Behavior |
|-----------|----------------|-----------------|-------------|----------------|
| `ingest` | — | `driver` | `alerts_created`, `incident_id`, `severity` | Fails on invalid payload |
| `context` | — | `checker_names` (list) | `checks_run`, `checks_passed`, `checks_failed`, `results` | Individual checker failures → `"unknown"` status, node continues |
| `intelligence` | `provider` | `provider_config` | `provider`, `recommendations`, `summary` | Fails on exception; use `"required": false` to make optional |
| `notify` | `drivers` (list) or `driver` (string) | — | `channels_attempted`, `channels_succeeded`, `deliveries` | Partial failure OK; errors only if ALL channels fail |
| `transform` | `source_node` | `extract`, `mapping`, `filter_priority` | `transformed`, `source_node` | Fails on exception |

**Output chaining:** Each node's output is stored in `ctx.previous_outputs[node_id]` and available to all downstream nodes. The `notify` node reads checker/intelligence outputs to build smart notification messages with derived severity.

**Key files:**
- `apps/orchestration/nodes/base.py` — `NodeContext`, `NodeResult`, `BaseNodeHandler`
- `apps/orchestration/nodes/context.py` — runs `CHECKER_REGISTRY` checkers
- `apps/orchestration/nodes/notify.py` — queries `NotificationChannel` DB records, uses `DRIVER_REGISTRY`
- `apps/orchestration/nodes/intelligence.py` — calls provider with timeout
- `apps/orchestration/nodes/ingest.py` — wraps `AlertOrchestrator`
- `apps/orchestration/nodes/transform.py` — extract/filter/map operations

## App layout rules (required)

- Endpoints must live under `apps/orchestration/views/` (endpoint/module-based).
  - Example: `views/pipeline.py`, `views/status.py`
- Tests must live under `apps/orchestration/_tests/` and mirror the module tree.
  - Example: `orchestrator.py` → `_tests/test_orchestrator.py` (or `_tests/orchestrator/test_pipeline.py`)
  - Example: `views/pipeline.py` → `_tests/views/test_pipeline.py`

## Doc vs code status

Tests have been migrated to `_tests/` (completed). Some code still uses monolithic `views.py`; migrate to `views/` package when touching related code.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.orchestration`, admin is the primary operations surface and should:
- Provide rich list/detail views for `PipelineRun` and `StageExecution` (filters, search, durations, attempts)
- Make it easy to traverse `PipelineRun` → `StageExecution` → linked `Incident`/artifacts
- Expose retry/failure context clearly (error type/message, retryable flag, attempt count)
- Ensure any stored payloads/prompts are redacted (show refs, not secrets)

## Security standards (audit-enforced)

Authoritative source: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/orchestration/` section. The orchestrator is post-API-key but still untrusted — every payload field originates from an API caller.

### Pipeline payload trust
- **Every field of every request body to `/orchestration/*` is untrusted** after API-key auth. This includes `payload`, `provider`, `provider_config`, `notify_driver`, `notify_config`, `notify_channel`, `incident_id`, `trace_id`, `checker_configs`, `labels`, `hostname`. Treat them as attacker-controlled in every executor and node handler.
- **`provider_config` is forwarded verbatim** to `apps.intelligence.providers.get_provider`. Any new path/URL/command/template-bearing kwarg added to a provider's `__init__` **must** be added to `apps.intelligence.providers.BLOCKED_CONFIG_KEYS` or validated at the constructor (see [Finding 1](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md) for the worked example).
- **`_PAYLOAD_TEMPLATE_KEYS`** (in `apps/orchestration/executors.py:34`) strips Jinja-template-bearing keys from payload-supplied notify config. Any new template-bearing key MUST be added to this set.

### Identifier discipline
- **`run_id` is always server-generated** (`uuid.uuid4()` in `PipelineOrchestrator.start_pipeline` and `DefinitionBasedOrchestrator.execute`). A caller-supplied `run_id` in the body is ignored. Do not introduce code paths that accept caller-chosen run IDs — they could collide existing records or forge `idempotency_key`s.
- **`trace_id` is caller-controllable**. It is a log-correlation hint only — **never** an authorization token. Never gate access, identity, or routing on its value.
- **`incident_id` is request-supplied without per-actor authorization** in `PipelineDefinitionExecuteView`. This is **the single-tenant assumption** — every API key has access to every incident. Document this and revisit before any multi-tenancy.

### `_should_skip` discipline
- `DefinitionBasedOrchestrator._should_skip()` supports a `skip_if_condition` string with a fixed `.has_errors` pattern. **This is a fixed-pattern matcher by design and MUST remain one.**
- Do **not** introduce `eval`, `exec`, `compile`, `ast.literal_eval` on attacker data, or Jinja2 evaluation here. `PipelineDefinition.config` is admin-controlled but admin-trust is not arbitrary-code-execution trust.
- If a richer condition language is genuinely needed, route it through an explicit safe-expression parser with no name resolution and no attribute access (e.g. an AST allowlist).

### Node handler contract
- **Every new node type's `validate_config` MUST be implemented** and called from `DefinitionBasedOrchestrator.validate()`. Nodes without it become attack surface via the admin-editable `PipelineDefinition.config`.
- **Node-type dispatch is via a fixed in-process registry** (`apps/orchestration/nodes/__init__.py:_NODE_HANDLERS`). Do not introduce string-based dynamic import.
- **Stage dispatch on `PipelineOrchestrator` is enum-keyed** — `self.executors[PipelineStage.X]`. Do not introduce string-based dispatch from payload.

### Celery + serialization
- `CELERY_ACCEPT_CONTENT = ["json"]`, `CELERY_TASK_SERIALIZER = "json"`, `CELERY_RESULT_SERIALIZER = "json"` are mandatory. **Never accept pickle** even temporarily — broker compromise scope is permanent if pickle is enabled even once during a migration.
- Tasks must remain JSON-serializable. No `dataclass(frozen=False)` instances or class types as task args.

### Audit checks before merging
- [ ] New executor / node handler does not call `eval`, `exec`, `compile`, or dynamic import.
- [ ] New payload field is documented as untrusted and routed through the relevant filter (`_PAYLOAD_TEMPLATE_KEYS`, `BLOCKED_CONFIG_KEYS`, or constructor validation).
- [ ] New node type has a `validate_config` and is registered in `_NODE_HANDLERS`.
- [ ] `run_id` is generated server-side (uuid4); `trace_id` is treated as a hint only.
- [ ] Celery settings unchanged: JSON-only accept / task / result.
- [ ] Run `uv run pytest apps/orchestration/_tests/` to confirm regression coverage holds.
