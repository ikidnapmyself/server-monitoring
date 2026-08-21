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
- `apps/alerts/services.py` — business logic (`AlertOrchestrator`, `IncidentManager`). `AlertOrchestrator` is **the** alert write path — see below.
- `apps/alerts/models.py` — `Alert`, `Incident`, `AlertHistory`, `Node`. `Node` is the **agent registry**, keyed by `instance_id` and upserted on each accepted cluster push (`Node.upsert`); the hub is *not* represented as a node. Alert/incident grouping resolves the owning node from the `instance_id` label (see `incident_instance_key` / `resolve_node` in `services.py`); the hub's own checker-generated runs carry no `instance_id` and group by `hostname`.
- `apps/alerts/timeline.py` — `build_incident_timeline(incident)`: a **pure** aggregator that merges `AlertHistory` + `StageExecution` + `PipelineRun` into one chronological list (with `trace_id`/`run_id`), rendered read-only + escaped as the "Merged chronological timeline" on the Incident admin. No models/queries with side effects.
- `apps/alerts/reevaluation.py` — **hub-side per-node severity re-evaluation (ingest-time)**. Nodes report raw metrics + a default severity; the hub recomputes severity against per-node policy in `Node.config` and overrides it. Called at the top of `AlertOrchestrator._process_alert` (covers create + update), so nodes stay unchanged. Fail-open: any missing/invalid input (or exception) passes the alert through unchanged. Two checker-types wired: numeric-threshold override for the 7 numeric checkers (`_score_numeric` + `PRIMARY_METRIC`) and a `listening_ports` **allowlist** evaluator (`_score_allowlist`, re-flagging the reported `listening` inventory against `Node.config["listening_ports"]["allowlist"]`; empty allowlist → exposed-only). Extend per checker-type by adding a pure scorer to `SCORERS` + an evaluator to `REEVALUATORS`. Overrides are audited in `annotations["severity_reevaluated"]`. The pure scorers in `SCORERS` are the shared, testable units reused by the config-change re-eval below. See `docs/plans/2026-08-07-hub-node-severity-reeval-design.md` and `docs/plans/2026-08-09-listening-ports-allowlist-reeval-design.md`.
- `apps/alerts/reeval_existing.py` — **hub-side re-evaluation of a node's EXISTING open alerts on config change** (operator-triggered, distinct from ingest-time above). Re-scores a node's firing alerts from their stored metrics by dispatching through the shared `SCORERS` registry (numeric + `listening_ports` allowlist), then on apply resolves / adjusts severity, writes `AlertHistory` + a distinct `annotations["reevaluated_on_config_change"]` audit key, and auto-resolves incidents whose alerts all resolved — all in one transaction; idempotent. `preview_node_alert_reeval` (no writes) / `apply_node_alert_reeval`. Two surfaces: the **Re-evaluate open alerts** Node admin action (confirmation dialog, gated on change permission) and the `reevaluate_node_alerts <instance_id>` management command (`--dry-run`, confirm prompt, `--noinput`). See `docs/plans/2026-08-08-reeval-existing-alerts-design.md`.
- `apps/alerts/materiality.py` — **the fan-out change gate**: one predicate,
  `is_material_change(...)`, answering "does this write deserve its own downstream
  pipeline run?" True when severity changed either way, status transitioned
  (firing↔resolved), or the context key moved. Called from exactly one place —
  `AlertOrchestrator._update_alert`, the only update path there is — which records the
  result on `ProcessingResult.material_alerts` (the bridge aggregates onto
  `CheckAlertResult.material_alerts`). The comparison happens *inside*
  the write path, because by the time a caller sees the result the old severity, status
  and key have already been overwritten. A new alert is always material. Deliberately
  excluded: `description` — for checker alerts it is `CheckResult.message`, which
  carries live metric values and would make every push look material. Deliberately NOT
  built on `AlertHistory` events: the two paths write different events, so a
  history-based gate would behave differently by origin (that reasoning predates the
  write-path unification and still holds — origin is a property of the caller, not of
  the write).
- `apps/alerts/context_keys.py` — **hub-side per-checker "what situation is this?" keys**,
  stored on `Alert.context_key` and compared by the gate. A registry keyed by the
  `checker` label (`KEY_BUILDERS`), mirroring `reevaluation.SCORERS` — *not* a
  `BaseChecker` method, because checkers run on nodes and the gate runs on the hub over
  `Alert` rows, so no node redeploy is involved. Reads metrics back out of annotations
  and normalises the two producers' shapes (`cluster` writes a JSON `metrics` blob,
  `check_integration` writes one `str(value)` per key). Keys are namespaced
  (`"listening_ports:22,8080"`; a clean scan is `"listening_ports:"`, never `""`) and
  digested above 200 chars so two port sets sharing a prefix cannot collapse. **Fails
  open**: an unknown checker, unparseable annotations or a raising builder all return
  `""`, which degrades to severity/status-only gating — over-notifying, never silencing.
  Add a checker by adding a pure builder to `KEY_BUILDERS`.
- `apps/alerts/metrics.py` — `parse_metrics(annotations)`, shared by `reevaluation` and
  `context_keys` so "read a node's metrics back" means one thing.
- `apps/alerts/urls.py` — URL routing for this app

## The alert write path (one, not two)

`AlertOrchestrator._process_alert` → `_create_alert` / `_update_alert` is the **only** code
that writes `Alert` rows. Both ingest sources reach it: webhooks via
`AlertOrchestrator.process_webhook`, and checker results (the hub's own cron *and* every node
push, i.e. all node traffic) via `CheckAlertBridge`.

`CheckAlertBridge` (`apps/alerts/check_integration.py`):
- **Does:** convert a `CheckResult` into a `ParsedAlert` (`check_result_to_parsed_alert`) —
  its actual job — then delegate to the orchestrator it already holds fully configured
  (`auto_create_incidents`, `auto_resolve_incidents`, `trace_id`, `create_from_resolved`),
  and aggregate `ProcessingResult` counters/lists onto `CheckAlertResult`.
- **Does not:** write alerts, write `AlertHistory`, touch incidents, or hold any write
  policy of its own. It configures the orchestrator; it does not second-guess it.
- The one checker-specific *policy* is `create_from_resolved=False`, a constructor flag on
  the orchestrator alongside its siblings: a first sighting that is already resolved opens
  no row. Checkers report every checker every tick, so a healthy one would otherwise open a
  resolved `Alert` row on its first run. Webhook traffic keeps the default `True`, where a
  resolved notification for an unseen alert is still a record.

**So: new alert-write behaviour goes in `AlertOrchestrator`, and reaches both paths from
there.** Do not add a create/update/resolve method to the bridge. The bridge used to carry
its own set and they drifted three separate times — a permanently empty
`CheckAlertResult.alerts`, materiality recorded on two of three bridge methods, and a refire
that never restored `status`/`ended_at`, so the `resolved-all-clear` lane delivered an
all-clear for a CRITICAL problem. Branch coverage caught none of them: both paths executed,
one just had no effect. See `docs/plans/2026-08-21-alert-write-path-unification-design.md`.

**The quiet re-push short-circuit.** `_process_alert` returns early, writing *nothing*, when
an already-`RESOLVED` alert is re-pushed as resolved. This is the one place the pipeline
deliberately records no evidence of an ingest, so know it is there before you go looking for
a missing `AlertHistory` row. Nodes push OK results every tick; running those through
`_update_alert` wrote an `updated` history row each time (~30k/day across a healthy fleet)
saying nothing, and never material, so no downstream run was ever involved. The *first*
resolve is a status transition and does not come through here.

**Refire reopens the incident.** In `_update_alert`, a `resolved → firing` transition calls
`Incident.reopen()` (a lifecycle method alongside `resolve()`/`close()`: status back to OPEN,
`resolved_at`/`closed_at` cleared, `summary` kept) when the alert's incident is RESOLVED or
CLOSED. CLOSED reopens too — one rule, nothing to remember. `Alert` rows are reused per
fingerprint and `_find_open_incident` only considers OPEN/ACKNOWLEDGED, so without this the
alert stays bolted to a resolved incident forever and notify reports an incident marked
resolved for something actively firing. The accompanying `refired` `AlertHistory` event is
what surfaces the reopen on the merged incident timeline.

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
