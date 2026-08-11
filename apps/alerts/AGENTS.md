# apps.alerts — Agent Notes

This file contains **app-local** guidance for working in `apps/alerts/`.

## Role in the pipeline

Stage: **ingest**

Responsibilities:
- Accept inbound alert payloads (webhooks)
- Validate + parse payloads via a driver
- Normalize into a common schema
- Create/update `Alert` + `Incident`
- Attach `trace_id/run_id` when invoked via orchestration

Output contract (to orchestrator):
- `{ incident_id, alert_fingerprint, severity, source, normalized_payload_ref }`

## Key modules

- `apps/alerts/drivers/` — payload parsers (Alertmanager, Grafana, PagerDuty, etc.)
  - Drivers should implement `validate()` and `parse()`.
- `apps/alerts/services.py` — business logic (`AlertOrchestrator`, `IncidentManager`)
- `apps/alerts/models.py` — `Alert`, `Incident`, `AlertHistory`, `Node`. `Node.is_self` flags the row representing **this hub itself** (the self-node); `Node.ensure_self()` idempotently upserts it from `settings.INSTANCE_ID` (no-op when unset), and the `bootstrap_self_node` management command runs it. The self-node makes the Node registry the uniform "which server" spine (hub + agents alike), orthogonal to pipeline *origin* (incoming vs checker-generated). See `docs/plans/2026-08-09-admin-hardening-design.md`.
- `apps/alerts/timeline.py` — `build_incident_timeline(incident)`: a **pure** aggregator that merges `AlertHistory` + `StageExecution` + `PipelineRun` into one chronological list (with `trace_id`/`run_id`), rendered read-only + escaped as the "Merged chronological timeline" on the Incident admin. No models/queries with side effects.
- `apps/alerts/reevaluation.py` — **hub-side per-node severity re-evaluation (ingest-time)**. Nodes report raw metrics + a default severity; the hub recomputes severity against per-node policy in `Node.config` and overrides it. Called at the top of `AlertOrchestrator._process_alert` (covers create + update), so nodes stay unchanged. Fail-open: any missing/invalid input (or exception) passes the alert through unchanged. Two checker-types wired: numeric-threshold override for the 7 numeric checkers (`_score_numeric` + `PRIMARY_METRIC`) and a `listening_ports` **allowlist** evaluator (`_score_allowlist`, re-flagging the reported `listening` inventory against `Node.config["listening_ports"]["allowlist"]`; empty allowlist → exposed-only). Extend per checker-type by adding a pure scorer to `SCORERS` + an evaluator to `REEVALUATORS`. Overrides are audited in `annotations["severity_reevaluated"]`. The pure scorers in `SCORERS` are the shared, testable units reused by the config-change re-eval below. See `docs/plans/2026-08-07-hub-node-severity-reeval-design.md` and `docs/plans/2026-08-09-listening-ports-allowlist-reeval-design.md`.
- `apps/alerts/reeval_existing.py` — **hub-side re-evaluation of a node's EXISTING open alerts on config change** (operator-triggered, distinct from ingest-time above). Re-scores a node's firing alerts from their stored metrics by dispatching through the shared `SCORERS` registry (numeric + `listening_ports` allowlist), then on apply resolves / adjusts severity, writes `AlertHistory` + a distinct `annotations["reevaluated_on_config_change"]` audit key, and auto-resolves incidents whose alerts all resolved — all in one transaction; idempotent. `preview_node_alert_reeval` (no writes) / `apply_node_alert_reeval`. Two surfaces: the **Re-evaluate open alerts** Node admin action (confirmation dialog, gated on change permission) and the `reevaluate_node_alerts <instance_id>` management command (`--dry-run`, confirm prompt, `--noinput`). See `docs/plans/2026-08-08-reeval-existing-alerts-design.md`.
- `apps/alerts/urls.py` — URL routing for this app

## Boundary rules

- **Do not** call downstream stages (`apps.checkers`, `apps.intelligence`, `apps.notify`) directly.
  - Only `apps.orchestration` advances the pipeline.
- Never log/store secrets from inbound payloads. Prefer storing **redacted refs**.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.alerts`, admin should make it easy to:
- Browse incidents and linked alerts efficiently (filters, search, list displays)
- Inspect alert lifecycle/audit trail (`AlertHistory`)
- Jump from an `Incident` to related pipeline runs/stage executions (via relationships/links when available)

## App layout rules (required)

- Endpoints must live under `apps/alerts/views/` (endpoint/module-based).
  - Example: `views/webhook.py`, `views/health.py`
- Tests must live under `apps/alerts/_tests/` and mirror the module tree being tested.
  - Example: `drivers/grafana.py` → `_tests/drivers/test_grafana.py`
  - Example: `views/webhook.py` → `_tests/views/test_webhook.py`

## Doc vs code status

Tests have been migrated to `_tests/` (completed). Some apps still use monolithic `views.py`; migrate to `views/` package when touching related code.

## Security standards (audit-enforced)

Authoritative source for the security threat model: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/alerts/` section. The webhook endpoint is the **only external trust boundary** in the system — any change in this app gets audited against the rules below.

### Rules for new drivers
- **Authentication is the API-key middleware, not the driver.** All non-GET webhook requests require a valid `Authorization: Bearer <token>` (or `X-API-Key`) when `API_KEY_AUTH_ENABLED=1`. Drivers do **not** implement their own signature check — the per-driver HMAC scaffold was removed. If a future vendor genuinely needs its own signature scheme, add it deliberately as that vendor's real algorithm, gated behind its own config, and audit it; do not resurrect a generic `WEBHOOK_SECRET_<DRIVER>` HMAC.
- **Use `hmac.compare_digest`** for any constant-time secret comparison you must perform locally. Never use `==` on token/signature bytes.
- **`validate()` and `parse()` must be pure** — no DB writes, no outbound HTTP, no subprocess. Driver auto-detection probes every registered `validate()` against an unknown payload; side effects in `validate()` become reachable by any caller who can hit `/alerts/webhook/`.
- **Never `str(e)` an exception into a production error response.** Use a fixed error string; log the full exception with `logger.exception()` keyed by `trace_id`. Echoing exception messages back to the caller is an information-disclosure vector (stack details, internal paths).

### Trust boundary discipline
- Webhook payloads are the canonical **external/untrusted** input. Treat every field as attacker-supplied even after auto-detect picks a driver — auto-detection only confirms the *shape* matches a known driver, not that the *sender* is authentic.
- Never log raw payloads or signature header values; per-field logging is fine for fingerprint/severity/source.
- Stored `Alert.payload_ref` and `Incident.normalized_payload_ref` are **references**, not raw payloads.

### Audit checks before merging
- [ ] New driver added: relies on the API-key middleware for auth (no bespoke signature check); any deliberate vendor-specific scheme is documented in `docs/Security.md` and audited.
- [ ] No `mark_safe` / `format_html` without `{}` placeholders in admin code.
- [ ] No `str(e)` returned in HTTP response bodies.
- [ ] Run `uv run pytest apps/alerts/_tests/` and confirm signature-verification tests still pass.
