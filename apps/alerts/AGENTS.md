# apps.alerts — Agent Notes

This file contains **app-local** guidance for working in `apps/alerts/`.

## Role in the pipeline

**Not a stage — a producer.** Ingest is not a pipeline stage. `apps.alerts` writes alert
truth and lets incidents form, then hands the materially changed incidents to
`apps.orchestration.intake.enqueue_for`, which records one `PENDING` run each. The
orchestrator takes it from there.

Responsibilities:
- Accept inbound alert payloads (webhooks)
- Validate + parse payloads via a driver
- Normalize into a common schema
- Create/update `Alert` + `Incident`
- Mint a `trace_id`, write it onto the alerts, and pass the same one to `enqueue_for`
- Enqueue one run per materially changed incident, and nothing more

**The webhook ingests on the request thread.** `apps/alerts/views.py` reads the body
(rejecting anything over `MAX_PAYLOAD_BYTES`), calls
`AlertOrchestrator.process_webhook`, then `enqueue_for`. Its response contract:

| Code | When | Body |
|---|---|---|
| 202 | alerts were written (or the payload legitimately yielded none — logged and accepted) | `{"status": "accepted", "trace_id": …, "incidents": [ids]}` |
| 400 | no driver claimed the payload, so nothing was written | error |
| 413 | body over `MAX_PAYLOAD_BYTES` | error |
| 500 | a driver resolved but nothing was written — the sender should retry | error |

The 400/500 split is the point: 400 means "this payload is unusable, do not retry"; 500
means "we should have stored this and did not". Errors arriving *alongside* written
alerts still return the success code, because a 5xx there would have the sender retry
work that already landed.

Output contract (to orchestrator): the enqueued run's payload,
`{"downstream_incident_id": N}`. The old `IngestResult` shape is built only by the
legacy `IngestExecutor`.

## Key modules

- `apps/alerts/drivers/` — payload parsers (Alertmanager, Grafana, PagerDuty, etc.)
  - Drivers should implement `validate()` and `parse()`.
- `apps/alerts/services.py` — business logic (`AlertOrchestrator`, `IncidentManager`). `AlertOrchestrator` is **the** alert write path — see below.
- `apps/alerts/models.py` — `Alert`, `Incident`, `AlertHistory`, `Node`. `Node` is the **registry of every machine that produces truth about itself**, keyed by `instance_id` and upserted (`Node.upsert`) on each accepted cluster push *and* on each local check run that records alerts here — so **the hub is a node in its own registry**. `last_source` distinguishes `cluster` (arrived by push) from `local` (registered by a local check run). Alert/incident grouping resolves the owning node from the `instance_id` label (see `incident_instance_key` / `resolve_node` in `services.py`), and hub-local checker alerts now carry that label like any node's.
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

## Checker-alert identity

A checker-origin alert is identified by **`check:{instance_id}:{checker_name}`**
(`apps/alerts/identity.py`, `checker_fingerprint`). `local_instance_id()` is this
machine's key: `settings.INSTANCE_ID`, falling back to the hostname when unconfigured.

- **Keyed on `instance_id`, not hostname.** Hostnames collide across stock installs
  (three fresh boxes all called `ubuntu`) and change on rename, either of which merges
  or splits histories that should not move. The instance id is the `Node` primary key,
  so it is the same thing the registry and the grouping code already key on.
- **`(fingerprint, source)` is the dedup pair** — the lookup in
  `AlertOrchestrator._process_alert` (`apps/alerts/services.py:269`). Both halves must
  agree across producers or one condition on one machine becomes two `Alert` rows, so
  **both producers use `source = "cluster"`**: `CheckAlertBridge.SOURCE_NAME` (it used
  to be `server-checkers`) and the payload `push_to_hub` builds. Migration
  `apps/alerts/migrations/0011_checker_alert_identity.py` rewrote existing rows onto
  this identity, parking collisions under `:legacy:<pk>` so two histories never merge — and
  resolving each parked row, since no producer will ever emit that key again to close it.
- **The hub derives it; it never trusts the push.** `ClusterDriver._parse_alert`
  recomputes `checker_fingerprint(instance_id, labels["checker"])` from the *envelope*
  it authenticated and ignores any `fingerprint` in the alert body. Auth is a shared
  API key with no per-node binding, so a body-supplied fingerprint would let any key
  holder push `check:some-other-node:cpu` and take over that machine's alert row,
  history and incident. `push_to_hub` computes the same value with the same helper,
  so an honest push is byte-identical. Cluster alerts with no `checker` label are not
  checker-origin and keep the provided-or-generated fingerprint. `instance_id` must be
  a non-blank string (`ClusterDriver.validate`, `register_pushing_node`) so a blank id
  cannot mint identity or a `Node` row.
- **The alert name is deliberately stable:** `f"{checker_name.upper()} Check Alert"`,
  written identically by the bridge and by `push_to_hub._result_to_alert`. Incident
  grouping matches on `alert.name` (`_find_open_incident`, `apps/alerts/services.py:517`),
  so a name carrying live metrics — the old `f"{checker}: {message}"` — drifted every
  tick and silently split one situation into a new incident per tick. Live text belongs in
  `description`; metrics belong in labels/annotations.

**Self-registration.** `CheckAlertBridge` upserts the machine it just checked into the
`Node` registry (`source="local"`) inside the same transaction, *before* writing alerts —
`_create_alert` resolves the node from the `instance_id` label and only links to an
already-registered row. `register_node=False` opts out, and `CheckExecutor` sets it from
the one condition that matters: whether the payload carries a `hostname`. A payload
hostname means the diagnosis is *about another machine* — the checkers run here but the
alerts are labelled with the subject incident's hostname — so that run must not claim that
identity for this machine's registry row.

The command producers set it the same way, from their own `--hostname`: with none given
(`check_health`, the scheduled job) the bridge is describing *this* machine, and it
registers. Note that `CheckExecutor`'s branch is currently unreachable for live runs — an
incident run's payload carries only `downstream_incident_id` — which is recorded under
"Known gaps" in `apps/orchestration/AGENTS.md`.

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

## Incident gate

`apps/alerts/incident_gate.py` — `follow_alert(incident, old_severity, new_severity,
old_status, new_status) -> (reopen, notify)`. `is_material_change` decides whether an
*alert* changed; the gate decides what that means for its *incident*: does it follow the
alert, and does anyone hear about it. `_update_alert` is the only caller, so both ingest
paths get one answer. One table, one place:

| Incident status | Alert change | Result |
|---|---|---|
| OPEN | any material | enqueue |
| ACKNOWLEDGED | alert resolved | enqueue the all-clear, stay ACKNOWLEDGED |
| ACKNOWLEDGED | severity **rose** | reopen (ACK → OPEN), enqueue — escalation breaks an ack |
| ACKNOWLEDGED | refire / same or lower severity | absorb: history row only, no run |
| RESOLVED / CLOSED | alert firing (refire **or** severity change) | reopen, enqueue |
| RESOLVED / CLOSED | alert resolved | absorb |

A reopen calls `Incident.reopen()` (status back to OPEN, `resolved_at`/`closed_at` cleared,
`summary` kept); the `refired` `AlertHistory` event surfaces it on the incident timeline. A
future snooze is one more row in this table and nothing else.

**Operator transitions go through `IncidentManager`** (`acknowledge` / `resolve` / `close`
in `services.py`; the admin actions are thin wrappers). Each transitions the row
synchronously and, in the same transaction, enqueues one `PENDING` run with
`origin=manual` via `apps.orchestration.inbox.enqueue_incident_runs` — it *announces*.
Nothing executes in-request; the next `process_inbox` drain delivers, and the headline
(`derive_headline`) reads the incident's live status, so the message says `[RESOLVED]`.
Do not flip `Incident.status` from anywhere else. Known silent exception:
`reeval_existing._resolve_incidents_for` (config-change re-eval) resolves without a run —
a follow-up, not a pattern to copy. See
`docs/plans/2026-08-24-incident-lifecycle-orchestration-design.md`.

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

### Node detail page

The `Node` change form is an operator overview, not a registry form. Its panels are built by
`apps/alerts/node_overview.py` as plain dataclasses, so they are testable without the admin,
and rendered by `templates/admin/alerts/node/change_form.html`, attached through
`NodeAdmin.render_change_form`.

- **Charts and preflight are local-node-only.** `CheckRun` and `PreflightRun` are written by
  the machine that ran them and are never pushed to a hub, so a peer has no rows here. A peer
  gets an explicit sentence saying so, never an empty chart: a blank chart reads as "flat",
  which is a lie about a machine nobody has data for.
- **The per-checker state table is the one panel that works for every node.** It reads
  `CheckRun` for the local node and the node's `Alert` rows for a peer, which is exactly the
  truth each one has.
- **The local node's freshness is informational, never amber**, mirroring the nodes card in
  `config/dashboard.py`. This instance's own `Node` row is upserted by its own local check
  runs, so scoring it like a peer would paint a healthy fleet permanently amber.

### Node policy form

`Node.config` is edited as typed boxes, never as a raw JSON widget. The shape lives in
`apps/alerts/node_policy.py` (plain functions and dataclasses, testable without the admin)
and the form is `NodePolicyForm` in `apps/alerts/forms.py`.

- **The field spec is derived, not restated.** `FIELD_SPECS` is built from
  `reevaluation.SCORERS` and `PRIMARY_METRIC`, so adding a scorer adds a form section with
  no edit to the form. A completeness test in `_tests/test_node_policy.py` guards it, the
  same way one guards `config/admin.py`'s `SECTION_MAP`.
- **Strict at the keyboard, fail-open at ingest.** `reevaluation.py` must never raise on a
  node's push, so it silently passes through any policy it cannot use. The editor rejects
  those same shapes outright, as field errors. A policy the runtime ignores is
  indistinguishable from no policy at all, which is the bug this closed.
- **Sections follow the node.** The form shows a section per checker the node reports or
  already configures. Config keys it has no spec for are preserved rather than deleted, and
  the read-only panel on the same page lists them as "Not honoured", alongside policy that
  is stored with the right keys but still scores nothing ("Saved but not scoring").
- **Saving a scoring-relevant change redirects to the re-evaluate preview**
  (`scoring_changed` decides), because a saved policy nobody re-evaluates does nothing to
  alerts already open.

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
