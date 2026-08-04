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

- `apps/orchestration/orchestrator.py` — pipeline implementation (`start_pipeline`, `execute_run`)
- `apps/orchestration/management/commands/process_inbox.py` — broker-free drain of PENDING runs
- `apps/orchestration/models.py` — `PipelineRun`, `StageExecution`
- `apps/orchestration/executors.py` / `dtos.py` — stage execution helpers and DTOs
- `apps/orchestration/urls.py` — URL routing

## Pipeline shape (routing spine)

There is one fixed stage order — `INGEST → CHECK → ANALYZE → NOTIFY` — run by
`PipelineOrchestrator` via the four executors in `executors.py`. The *shape* is data:
after INGEST, the orchestrator resolves the matching `PipelineDefinition` for the
incident (`routing.py`, first-match-wins by `priority`) and runs the stages its
`run_checkers`/`run_intelligence`/`run_notify` flags enable; `notify` sends to the
matched pipeline's channels. The legacy node/edge graph (`DefinitionBasedOrchestrator`,
the `nodes/` package, `PipelineDefinition.config`) was **retired in Phase D** — do not
reintroduce it.

**Observability (projections, no new models):** journey panel on the Alert/Incident
admin; `manage.py trace <alert|trace_id>` (the chain); `manage.py report` (per-node
incidents, per-pipeline routing hits, inbox depth).

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
- **`run_id` is always server-generated** (`uuid.uuid4()` in `PipelineOrchestrator.start_pipeline`). A caller-supplied `run_id` in the body is ignored. Do not introduce code paths that accept caller-chosen run IDs — they could collide existing records or forge `idempotency_key`s.
- **`trace_id` is caller-controllable**. It is a log-correlation hint only — **never** an authorization token. Never gate access, identity, or routing on its value.
- **`incident_id` is request-supplied without per-actor authorization**. This is **the single-tenant assumption** — every API key has access to every incident. Document this and revisit before any multi-tenancy.

### Dispatch discipline
- **Stage dispatch on `PipelineOrchestrator` is enum-keyed** — `self.executors[PipelineStage.X]`. Do not introduce string-based dispatch from payload.
- **Routing `match` conditions fail closed** (`PipelineDefinition.matches`): unknown ops / bad shapes never match. Keep it a fixed operator set (`is/is-not/in/not-in`); do not add `eval`/expression languages over the admin-editable `match`.

### Durable ingest / drain (broker-free)
- The pipeline runs **without Celery/Redis**. The webhook records a `PENDING` `PipelineRun` (payload stored on `inbound_payload`) and `manage.py process_inbox` claims it (atomic `PENDING → PROCESSING`) and runs it via `execute_run`.
- `inbound_payload` MUST stay JSON-serializable (it is a `JSONField`). No class instances or non-JSON types — the stored payload is untrusted input, validated/parsed by each stage, never `eval`'d.
- Do **not** reintroduce a broker/queue or pickle-based serialization; single-hop fan-in has no need for one.

### Audit checks before merging
- [ ] New executor does not call `eval`, `exec`, `compile`, or dynamic import.
- [ ] New payload field is documented as untrusted and routed through the relevant filter (`_PAYLOAD_TEMPLATE_KEYS`, `BLOCKED_CONFIG_KEYS`, or constructor validation).
- [ ] New routing `match` operator (if any) fails closed and needs no expression evaluation.
- [ ] `run_id` is generated server-side (uuid4); `trace_id` is treated as a hint only.
- [ ] `inbound_payload` stays JSON-serializable; no broker/pickle reintroduced.
- [ ] Run `uv run pytest apps/orchestration/_tests/` to confirm regression coverage holds.
