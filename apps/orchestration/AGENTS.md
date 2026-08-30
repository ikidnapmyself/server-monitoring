# apps.orchestration — Agent Notes

This file contains **app-local** guidance for working in `apps/orchestration/`.

## Role in the pipeline

This app is the **only pipeline controller**.

Core rule: **one orchestrator, one trace**
- Only `apps.orchestration` is allowed to move work across stages.
- Every run must propagate `trace_id/run_id` across logs, monitoring, DB records, and outbound notifications.

## Responsibilities

The orchestrator owns:
- State machine: `PENDING → (INGESTED floor) → CHECKED? → ANALYZED? → NOTIFIED?` (+ failure/retry
  states). `INGESTED` is the status floor a run starts from, not evidence an INGEST stage ran;
  which of the three real stages follow is the matched lane's ordered `stages` list
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
- `apps/orchestration/management/commands/process_inbox.py` — broker-free drain of PENDING runs (thin CLI wrapper over `inbox.py`)
- `apps/orchestration/intake.py` — **the front door for producers**: `enqueue_for(result, *, trace_id, origin, …, sync=False)` reads `result.material_alerts`, resolves the materially changed incidents, and enqueues one run each. `sync=True` drains them before returning.
- `apps/orchestration/inbox.py` — **single source of truth** for the enqueue/drain/reclaim logic: `enqueue_incident_runs(incident_ids, …)`, `drain_runs(runs, orchestrator=…)` (the scoped synchronous drain `sync=True` uses), `claim` (atomic PENDING→PROCESSING CAS), `drain`, `drain_run`, `reclaim_stuck(pks=…)`, and `DEFAULT_STALE_MINUTES`. Both the `process_inbox` command and the admin Inbox actions call these — never duplicate the claim.
- `apps/orchestration/models.py` — `PipelineRun`, `StageExecution`. `PipelineRun.node` (FK → `alerts.Node`, the server the run concerns) and `PipelineRun.origin` (`incoming_webhook`/`checker_generated`/`manual`) power admin filtering by node+origin+status. `origin` is now passed by each producer into `enqueue_for`; `node` is passed only by `IncidentManager._announce`, so it is NULL on runs from the other producers — see "Known gaps" below. `InboxItem` is a proxy over `PipelineRun` filtered to PENDING/PROCESSING (the admin Inbox monitor, with age/`is_stuck` + drain/reclaim actions). See `docs/plans/2026-08-09-admin-hardening-design.md`.
- `apps/orchestration/executors.py` / `dtos.py` — stage execution helpers and DTOs
- `apps/orchestration/urls.py` — URL routing

## Pipeline shape (routing spine)

There are three stages — `CHECK → ANALYZE → NOTIFY` — run by `PipelineOrchestrator`
via the executors in `executors.py`. (`IngestExecutor` survives for the legacy branch
only; see "Entry stages are legacy" below.) The *shape* is data: **a run is an
incident**, and before any stage runs the orchestrator resolves the matching
`PipelineDefinition` from that incident's subject alert (`routing.py`, first-match-wins
by `priority`, ties on `id`) and runs the downstream stages listed in its `stages` column, in that order;
`notify` sends to the matched pipeline's `channel` — a single FK, because delivery has
never fanned out. **`PipelineDefinition.routed_channel()` is the one rule for whether a
lane delivers**: it returns the channel only when the FK is set *and* the channel is
active. Every reader — the executor, the readiness panel, preflight, the seed — asks it
that way rather than re-deriving "active", so they cannot drift. A lane that lists
`notify` and whose `routed_channel()` is `None` no longer falls back to payload-driven
selection; it fails `no_channel` (see below).

**The unit of work is an incident event, not a push.** A producer does not orchestrate:
it writes alerts and returns. Every incident it *materially changed* becomes its own
PENDING `PipelineRun` carrying `{"downstream_incident_id": <id>}` as its
`inbound_payload`. Each run resolves its **own** lane from its own incident's subject
alert and runs exactly that lane's stages. Runs inherit the producer's `trace_id` (and
origin, source, environment) with their own `run_id`, so one push is still one story in
`manage.py trace` and no parent FK exists. Before this, a push collapsed
to a single subject and the other incidents were opened, counted, and then silently
dropped — no lane, no analysis, no message. See
`docs/plans/2026-08-19-incident-fanout-design.md`.

**Draining is a mode, not a second path.** By default the runs wait for
`process_inbox`, which is what makes N analyses N independently claimed, independently
retryable runs bounded by `--limit`, instead of an unbounded loop held open inside one
run whose crash would lose the lot. `enqueue_for(..., sync=True)` drains them inline
instead, through `inbox.drain_runs`: it claims through `inbox.claim` (so a concurrent
`process_inbox` can never double-execute them) but executes through the caller's
orchestrator, so its retry/backoff settings and executors apply. Three callers ask for
it — `check_health`, the `run_pipeline` command, and `POST /orchestration/pipeline/sync/`
— because each has an operator waiting on the result. `execute_run()` deliberately does
not drain: `process_inbox` already is the drain.

**`apps.orchestration.intake.enqueue_for` is the single producer entry.** It reads
`result.material_alerts`, resolves the materially changed incidents, and calls
`inbox.enqueue_incident_runs`. Five producers walk through it — the alerts webhook,
`POST /orchestration/pipeline/`, `push_to_hub --local`, `check_health` and
`run_pipeline`. The sixth, `apps.alerts.services.IncidentManager._announce`
(`acknowledge` / `resolve` / `close`, `origin=manual`, fresh `trace_id`, and the only
caller that sets `node`), calls `enqueue_incident_runs` directly, because it holds an
incident id rather than a result with `material_alerts`. That is a gap in `enqueue_for`'s
contract, not a licence to add a seventh path: do not enqueue a `PipelineRun` by hand
elsewhere. See `docs/plans/2026-08-24-incident-lifecycle-orchestration-design.md`.

Two consequences worth knowing before changing this code:

- **A `no_route` fails the incident run**, and nothing else. The producer already
  returned successfully; the run an operator sees FAILED is the one carrying the
  unroutable incident.
- **A downstream run resumes on its stored payload.** `resume_pipeline` prefers
  `inbound_payload` when it carries the downstream marker, because the resume endpoint
  builds its payload from the request body, which cannot describe a child.
- **A double-run of the entry stage is no longer possible**, because there is no entry
  stage: a run executes exactly the stages its lane lists, once each.

**INGEST is legacy.** `IngestExecutor` still exists in `execute_run`, and a `PENDING`
run recorded before a deploy — one whose payload is a legacy `{"driver", "payload"}`
pair — must still drain through it, so pushes in flight at deploy time are not lost.
No producer creates such a run any more, and the branch dies once no such rows remain.
The `INGEST` enum value is kept for the same reason: historical `StageExecution` rows
must still render, and `apps/alerts/diagnosis.py` reports the stage only for incidents
that have a legacy run. CHECK is **not** legacy — it is a live lane stage. Producing an alert is not a pipeline stage: the webhook,
`push_to_hub --local`, `check_health`, `run_pipeline` and `POST /orchestration/pipeline/`
each write their alerts themselves, let incidents form, and enqueue one run per
materially changed incident through `apps.orchestration.intake.enqueue_for`. Do not add
a caller of the entry-stage path.

**`POST /orchestration/pipeline/` is a producer too**, and its two routes are exactly
the `sync` flag: `/pipeline/` leaves the runs `PENDING` for `process_inbox` and returns
202; `/pipeline/sync/` drains them and returns 200. Neither returns a `run_id` — a call
produces a run per materially changed incident, several or none — so both name the
`trace_id` and the incident ids instead. Its status codes are the webhook's: 400 when no
driver claimed the payload, 500 when a resolved driver wrote nothing, and the success
code when errors arrived alongside written alerts (a 5xx there would have the sender
retry work that already landed).

**`run_pipeline` is a producer like the rest.** `--sample` / `--payload` / `--file`
replay a webhook-shaped payload: `AlertOrchestrator.process_webhook`, then
`enqueue_for(origin=manual, sync=True)`. The only difference from the webhook is
`sync` — a CLI caller expects one command to finish, so it drains the runs it just
enqueued (claimed through `inbox.claim`, so a concurrent `process_inbox` can never
double-execute them). An unroutable payload is a `CommandError`: nothing was written
and only the operator can fix it.

**`--checks-only` is deprecated in favour of `check_health`**, and kept anyway — an
operator SSHes into a node and runs it by hand, and a removed command is a worse
surprise than a warning on stderr. It does the same work through the same path:
`CheckAlertBridge` built from `--checkers` / `--hostname` / `--label` /
`--warning-threshold` / `--critical-threshold`, then
`enqueue_for(origin=checker_generated, sync=True)`. `--no-incidents` needs no special
case: the bridge creates no incidents, so no alert has one, so nothing is enqueued and
nothing routes — the old "just check, do not disturb anything" contract, arrived at
rather than coded for. `--no-notify` is the narrower one: the lane still matches and
its stages still run, minus NOTIFY — SSH in, look at a node in real time, read the
local provider's suggestions, page nobody. It travels into the enqueued runs, because
NOTIFY runs there and not on the run the operator invoked.

`stages` is an ordered subset of `["check", "analyze", "notify"]` —
`PipelineDefinition.ROUTABLE_STAGES`. It deliberately excludes `ingest`: the alert a
lane is resolved *from* was written by a producer before the run existed, so there is
nothing for a lane to control there. Read it via `PipelineDefinition.routable_stages()`, never the raw column —
`clean()` only runs on admin forms, so fixtures and shell edits can persist junk.

**CHECK takes the incident as its subject.** For an incident run the diagnose stage
derives its checkers from the distinct `checker` labels on that incident's alerts,
filtered to the local `CHECKER_REGISTRY` (`executors._incident_checker_names`). An
incident that names no checkers runs none. It used to sweep the whole registry, which
made a lane merely listing `check` re-diagnose the entire machine on every incident.

**`checker_names=None` means "every checker"; `checker_names=[]` means "none".** This is
the contract of `CheckAlertBridge.run_checks_and_alert`, and it now carries real weight:
an incident that names no checkers passes `[]`, and must run nothing. `None` is what a
caller with no opinion passes — `check_health` with no positional arguments, say — and
the bridge expands it to the full registry. Never normalise one into the other, and never
write `checker_names or list(CHECKER_REGISTRY)`: that is exactly the collapse that turns
"diagnose nothing" back into "diagnose everything".

### Known gaps

These are real divergences between this document's promises and the code. They are
recorded rather than quietly dropped:

- **`PipelineRun.node` is NULL on most runs.** `enqueue_for` accepts `node` and no
  producer except `IncidentManager._announce` passes it, so admin filtering by node and
  `manage.py report`'s per-node view are thinner than the field's help text promises.
  The incident's subject alert already carries `node`.
- **`PipelineRun.alert_fingerprint` is blank on live runs.** It is written only in the
  legacy INGEST branch, so the `alert_fingerprint` tag this file lists as required is
  empty on every incident run's signals, and admin search by fingerprint over
  `PipelineRun` finds nothing.
- **`PipelineRun.environment` is `""` for producers that omit it** (the webhook,
  `push_to_hub --local`, `check_health`, `_announce`), rather than the model default.
- **`CheckExecutor`'s "about another machine" branch is unreachable.** It keys off a
  `hostname` in the run payload, and an incident run's payload carries only
  `downstream_incident_id` (plus `no_notify`), so a hub diagnosing a remote node's
  incident attributes the alerts CHECK writes to the hub itself.
- **`run_pipeline --notify-driver` is accepted and never read**; delivery is chosen by
  the matched lane's channel.

**Routing facts come from ONE alert** (`facts_from_alert(alert, origin)`), never merged
across an incident: `source`, `severity` (that alert's own), `status`
(`firing`/`resolved`), `instance` (`instance_id` → `instance` → `hostname`, via
`instance_key_from_labels`), `labels`, and `origin`
(`incoming_webhook`/`checker_generated`/`manual`). Add a fact here and the admin's
Routing help text must name it — a completeness test in `_tests/test_admin.py`
enforces that, because an undiscoverable fact is one no operator can route on.

**There is no implicit fallback — in either half of the routing table.** The table can
fail to say where work goes in exactly three ways, and all three raise the same
non-retryable `StageExecutionError` through `routing_gap(stage, code, detail)` in
`apps/orchestration/errors.py`:

| Code | Missing | Raised by |
|---|---|---|
| `no_route` | no lane matched the alert | `_downstream_or_fail` (stage `routing`) |
| `no_channel` | the matched lane names no active channel | `NotifyExecutor.execute` |
| `no_driver` | the lane's channel names a driver that is not in `DRIVER_REGISTRY` | `NotifyExecutor.execute` |

The set is closed: it is the routing table's own structure, not an open-ended list of
missing components. **None is retryable** — the alert is stuck until an operator edits a
row, so a retryable failure would just spin (`no_driver` used to: three attempts, then a
"Mark for Retry" button, against a typo). `routing_gap` exists so that reasoning is
stated once; `code` leads the message because operators and log searches key on it, and
the `no_driver` message carries the registered driver names because that is what turns
the code into a fix. `StageExecutionError` lives in `errors.py`, not `orchestrator.py`,
because `orchestrator` imports `executors` — an executor could not otherwise import it at
module level. `orchestrator.py` re-exports it for existing importers.

`no_route` is attributed to `routing` rather than to a stage — it is raised before any
stage runs, from the run's incident's subject alert.
Do not reintroduce a default stage order in Python: migration `0012`
seeds `cluster-nodes` (priority 50, `source is cluster`, `["analyze", "notify"]`) and
`catch-all` (priority 1000, empty match, full order), and `0016` seeds
`resolved-all-clear` (priority 40, `status is resolved`, `["notify"]` — an all-clear
has nothing left to diagnose, and it sits above `cluster-nodes` so a resolved node
alert takes it rather than paying for an analysis). None of these rows are special-
cased in code; `apps/orchestration/testing.py` documents their effect on tests.

**The seed reads the hub instead of assuming it.** `0017` re-seeds the full default set
through `apps/orchestration/seeding.py`, shaped by how many channels are active: zero
means `notify` is dropped from every lane and `resolved-all-clear` is seeded inactive, so
a **recording hub** records rather than failing `no_channel` for an intent it never
expressed; one means `notify` is listed and bound to it; two or more lists `notify` and
binds nothing, because picking by name is the bug being removed. **A channel is optional;
a lane that lists `notify` is not.** `get_or_create` on `name` means an operator's row is
never rewritten, and binding only fills a `channel` that is `NULL` — the seed body is
shared with its tests (models are passed in) rather than living unexercised inside a
migration. Anything that reports on delivery must first ask whether the lane delivers at
all (`"notify" in lane.routable_stages()`): a lane that lists no stages is not broken,
it is quiet by design.

**Two read-only surfaces say so before an incident does.** The readiness panel's
`lane_channels` entry (`config/dashboard.py:build_readiness`) is `error` only when a lane
claims `notify` and `routed_channel()` is `None`; `info` ("recording only") when no lane
delivers and no channel is active — a supported way to run this hub, never red; and `ok`
otherwise, with a nudge in `detail` when a channel exists that no lane points at.
`check_pipeline_state` reports the same finding as a `warn` naming the offending lanes.
See `docs/plans/2026-08-22-lane-channel-required-design.md`.

**The hub is a node, so it has no lane of its own.** `0014` seeded `hub-self-check`
(priority 50, `origin is checker_generated`, empty `stages`) to keep a five-minute cron
from re-reporting a still-firing alert; the incident change gate closed that, and once
checker alerts adopted `source: cluster` the lane became a silent rival to `cluster-nodes`
at the same priority, settled by nothing better than row `id`. `0018` retires it — by
setting `is_active=False`, never deleting: `Incident.pipeline` is `SET_NULL`, so a delete
would blank which lane handled every incident it ever routed. A hub-local check run now
takes `cluster-nodes` and can page about the hub's own full disk. `check_health` (and
the deprecated `run_pipeline --checks-only`) carries the same origin and analyses and
notifies too, which is intended.

The legacy node/edge graph (`DefinitionBasedOrchestrator`, the `nodes/` package,
`PipelineDefinition.config`) was **retired in Phase D** — do not reintroduce it.

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
- The pipeline runs **without Celery/Redis**. The webhook ingests inline — it writes the payload's alerts, lets incidents form — and records one `PENDING` `PipelineRun` per materially changed incident, with `inbound_payload = {"downstream_incident_id": N}`; `manage.py process_inbox` claims each (atomic `PENDING → PROCESSING`) and runs it via `execute_run`. The stored payload of a live run is therefore ours, not the sender's; only a legacy `{"driver", "payload"}` run still carries an untrusted inbound body, and it is still treated as untrusted.
- `inbound_payload` MUST stay JSON-serializable (it is a `JSONField`). No class instances or non-JSON types — the stored payload is untrusted input, validated/parsed by each stage, never `eval`'d.
- Do **not** reintroduce a broker/queue or pickle-based serialization; single-hop fan-in has no need for one.

### Audit checks before merging
- [ ] New executor does not call `eval`, `exec`, `compile`, or dynamic import.
- [ ] New payload field is documented as untrusted and routed through the relevant filter (`_PAYLOAD_TEMPLATE_KEYS`, `BLOCKED_CONFIG_KEYS`, or constructor validation).
- [ ] New routing `match` operator (if any) fails closed and needs no expression evaluation.
- [ ] `run_id` is generated server-side (uuid4); `trace_id` is treated as a hint only.
- [ ] `inbound_payload` stays JSON-serializable; no broker/pickle reintroduced.
- [ ] Run `uv run pytest apps/orchestration/_tests/` to confirm regression coverage holds.
