---
title: "Routing Simplification"
parent: Plans
---

# Routing Simplification

**Status:** design approved — not yet planned, not yet implemented.
**Date:** 2026-08-12 (open questions closed 2026-08-13)

Routing decisions today are split between database rows and Python defaults. This design
moves them entirely into rows, so "why did this alert go there?" is answerable by reading the
`PipelineDefinition` table rather than the orchestrator.

---

## 1. How we got here

The session started from a different question: Django admin is not enough, so what should a
custom dashboard look like? The requirements were a routing/orchestration map plus a set of
cards (open alerts, recently resolved, warnings, trends).

Working through it produced three conclusions, in order:

1. **The cards are commodity.** Grafana or an equivalent does them better than we would. They
   should leave the codebase rather than be rebuilt.
2. **The map is not commodity.** No off-the-shelf tool can render it, because the routing
   semantics are Python (`PipelineDefinition.matches()`, `_downstream_stages()`), not data a
   query can express. Node-graph products all expect nodes and edges already computed — and
   computing them *is* the work.
3. **Designing the map X-rayed the routing model.** Drawing it honestly required representing
   several behaviours that turned out to be defects rather than features. That reframed the
   work: simplify routing first, then the map becomes both easy to draw and the proof the
   simplification landed.

---

## 2. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Metric history storage | No new tables; reconstruct from `AlertHistory` | Verified lossless — see §5 |
| Dashboard surface | New app outside admin, later narrowed to map-only | Leaf app, imported by nobody |
| Liveness | Static snapshot, refresh by reload | CSS handles the "approaching" motion; no transport layer |
| Node-free signature | Computed at read time, single promotable function | No migration until cardinality is known |
| Cards and trends | Grafana or equivalent, fed by a `notify` driver | Outbound sink fits the Driver pattern |
| Metrics egress / TSDB | **Parked** | Running a TSDB for 8 nodes is the biggest single cost; decide separately |
| Routing scope | **Full simplify** | Everything passes through orchestration; no backdoors |
| CHECK-skip invariant (§6) | **Pure data, no guard** | The lane's stage list is the whole truth; the engine never special-cases cluster |
| Unmatched traffic | **Seeded catch-all row**, not a quarantine state | Makes the fallback visible instead of relocating it |
| Entry point | **A fact in `match`** (`origin`), not an entry in `stages` | Entry points vary; they are matched on, not executed |
| Checker-generated runs | **Routed** | The hub's own scheduled checks currently notify nobody — see 3.9 |
| Retention (3.7) | **Separate work** | No shared code with routing; folding it in doubles the review surface |
| Push fan-out (3.10) | **Separate work** | Lives in the run/stage schema, not in routing — see §9 |

### On the visualization split

Pushing metrics *out* to a sink belongs in `apps/notify` — it is outbound external I/O, it
fits `BaseDriver`, it is configured through `NotificationChannel`, and the notify stage
already runs on every cluster push including all-OK ones (verified: `NotifyExecutor.execute`
runs even with no incident; `ctx.payload` carries every alert's metrics).

The dashboard *surface* does not belong in `notify`. It is a read projection across alerts,
checkers, intelligence, orchestration and nodes. Putting it inside a pipeline stage would make
that stage import from every other app — the exact inversion `AGENTS.md` scope-discipline
rule 3 names as the over-build signature. The map stays a leaf: it imports from everyone,
nobody imports it.

---

## 3. Loose ends found

Numbered for reference. Each was verified against the code, not inferred.

### 3.1 Implicit fallback lane — must go

`_downstream_stages` (`apps/orchestration/orchestrator.py:498-531`) returns a default of
`[CHECK,] ANALYZE, NOTIFY` when there is no incident **or** when no pipeline matches:

```
if not incident_id:
    return default
...
matched = resolve_pipeline(facts_from_incident(incident))
if matched is None:
    return default
```

So traffic matching no configured lane still analyses and notifies. This is behaviour with no
configuration behind it — invisible in the DB, unrepresentable on a map drawn from
`PipelineDefinition` rows.

The defect is the **invisibility**, not the processing. Fixed by §7.3 with a seeded catch-all
row, which preserves the behaviour while making it configuration.

### 3.2 Two modules decide whether checkers run

`skip_checkers` is declared on the driver (`apps/alerts/drivers/base.py:71`, set `True` in
`apps/alerts/drivers/cluster.py:43`), carried into the wrapper payload by the webhook view
(`apps/alerts/views.py:81-83`), read back in the orchestrator
(`apps/orchestration/orchestrator.py:325`), and finally reconciled against the lane's
`run_checkers` flag:

```
if matched.run_checkers and not skip_checkers:
    stages.append(PipelineStage.CHECK)
```

Answering "do checkers run for this traffic?" requires reading two modules and a payload key.
Resolved by §6.

### 3.3 The channels M2M is a lie

`PipelineDefinition.channels` is a `ManyToManyField`, which reads as fan-out. Delivery does
this (`apps/orchestration/executors.py:312`):

```
channel = pipeline.channels.filter(is_active=True).order_by("name").first()
```

Exactly one channel is ever notified: the alphabetically first active one. A pipeline
configured with three channels silently uses one. Config that looks like it works.

### 3.4 Routing facts are assembled from two different alerts

`facts_from_incident` (`apps/orchestration/routing.py:11-25`) iterates every alert on the
incident:

```
for alert in Alert.objects.filter(incident=incident):
    labels.update(alert.labels or {})
    source = source or alert.source
```

`Alert.Meta.ordering` is `["-received_at"]` (`apps/alerts/models.py:139`), so iteration is
newest-first. Therefore **labels come from the oldest alert** (later `update()` calls overwrite
earlier ones) while **source comes from the newest** (first non-empty wins). For a multi-alert
incident the routing input is a mashup of two different alerts.

*Correction to an earlier draft:* this note previously also claimed `instance` was "whichever
node fired first". That is no longer true — see §4.

### 3.5 Routing is resolved twice

Once after ingest — where the matched pipeline is stamped onto `incident.pipeline` — and again
in notify, which re-reads `incident.pipeline` in `_route_incident`. Two resolution points for
one decision.

### 3.6 Fingerprints are node-bound by construction

`generate_fingerprint` (`apps/alerts/drivers/base.py:81`) hashes name plus sorted labels, and
both the cluster driver and `check_integration` always inject `hostname` / `instance_id` into
labels. There is therefore no node-free identity to group or route on. "The same alert firing
on five nodes" is currently five unrelated fingerprints.

### 3.7 No retention anywhere

`AlertHistory`, `PipelineRun` and `PreflightRun` all grow unbounded. There is a "future prune"
TODO at `apps/checkers/management/commands/preflight.py:69` and nothing else. Not a routing
issue. **Scoped out** to its own work — but it gains a dependency: §9's fan-out follow-up must
decide retention alongside it, because fan-out is what makes run volume grow.

### 3.8 `reeval_existing` bypasses orchestration entirely

`apply_node_alert_reeval` (`apps/alerts/reeval_existing.py:88`) mutates `Alert.severity`,
writes `AlertHistory`, and via `_resolve_incidents_for` (`:141`) **resolves incidents** — with
no `PipelineRun`, no `trace_id`, and no notification.

A node config change can therefore silently close an incident and tell nobody. It terminates
incidents rather than merely routing them, and it breaks the "given a notification, jump back
to the exact incident" guarantee in `AGENTS.md`.

### 3.9 The hub does not monitor itself

`bin/install/cron.sh:74` installs this on every machine, on a default five-minute schedule:

```
run_pipeline --checks-only --json
```

`checks_only` sets the stage list to `[CHECK]` and the terminal status to `CHECKED`
(`orchestrator.py:324-327`). The CHECK stage creates alerts and incidents through
`CheckAlertBridge` (`executors.py:127`). So the run opens incidents and then stops: no
ANALYZE, no NOTIFY, and no lane is ever resolved, because routing is only reached after INGEST.

Nodes are unaffected in practice — a separate optional cron (`cron.sh:128`) runs `push_to_hub`,
and the hub then runs the full pipeline on their results. **The hub's own health is the hole.**
The machine watching eight nodes opens incidents about its own disk and memory every five
minutes and notifies nobody.

This is the same backdoor class as 3.8, on the default install path.

### 3.10 One push routes exactly one incident — *deferred, see §9*

`IngestExecutor` (`apps/orchestration/executors.py:91-96`) selects the routed subject with a
**global** query, scoped only by source and not to the alerts this run ingested:

```
alert_qs = Alert.objects.order_by("-received_at")
if ctx.source and ctx.source != "unknown":
    alert_qs = alert_qs.filter(source=ctx.source)
latest_alert = alert_qs.select_related("incident").first()
```

Two consequences:

1. **N-1 incidents are dropped by the run.** Every firing alert creates or attaches an incident
   (`apps/alerts/services.py:271-272`), and grouping is `(name, instance)`-scoped, so a node
   with three firing checkers produces three incidents. Only one reaches ANALYZE and NOTIFY,
   and `NotifyExecutor` sends a single message derived from one headline (`executors.py:337`).
   Three problems, one notification.
2. **Concurrent same-source pushes cross-contaminate.** Two nodes both posting as
   `source=cluster` means one run can route on the other's alert. Conceded in the comment at
   `executors.py:87-90`.

Consequence (2) is fixed here (§7.2). Consequence (1) is deferred — see §9.

### 3.11 The host is unreadable in the alerts admin

`AlertAdmin.list_display` includes `node` (`apps/alerts/admin.py:89`), but `node` is populated
only for cluster pushes: `resolve_node` (`services.py:33-44`) requires an `instance_id` label
*and* an already-registered `Node`, and only `Node.upsert` on a cluster push creates those.
Alerts arriving by webhook from Grafana or Alertmanager carry `instance` or `hostname` instead,
so the column renders blank and the server name stays buried in `labels`.

---

## 4. Non-findings (checked, and fine)

Recorded so they are not re-investigated later.

- **Incident grouping is already fixed and already unified.** An earlier draft carried this as
  an open question. Verified otherwise: `check_integration.py:302` delegates to
  `AlertOrchestrator._create_or_attach_incident`, so there is one implementation, not two.
  Grouping is `(name, instance)`-scoped through `incident_instance_key`
  (`apps/alerts/services.py:82`, `_find_open_incident:398-401`), so two hosts firing the same
  alert are two incidents. This also means every alert on an incident shares an instance key,
  which is why the `instance` claim was struck from 3.4.
- **The checker path is already clean.** `CheckAlertBridge` — the only code turning check
  results into alerts — is invoked from exactly one place, `apps/orchestration/executors.py:127`,
  the CHECK stage. `check_health` and `run_check` are report-only: they print and exit without
  creating alerts.
- **`skip_checkers` is not attacker-controlled.** The webhook nests the untrusted request body
  under `"payload"` and sets the flag itself from the resolved driver
  (`apps/alerts/views.py:81-83`). A posted body cannot reach the top-level flag.
- **Diagnostic runs already have a silence flag.** `--no-incidents` suppresses incident creation
  in the CHECK stage (`executors.py:131-136`), so routing checker-generated runs (§7.6) does not
  strand operators who want a quiet manual check.
- **Preflight is not an exception to the orchestration rule.** It emits no alerts and sends
  nothing; it writes `PreflightRun` / `PreflightCheck` rows and reports readiness. It is not
  pipeline traffic, so it needs no carve-out from a rule about pipeline traffic.
- **No new status is needed for unmatched traffic.** `mark_failed(..., retryable=False)` already
  exists (`apps/orchestration/models.py:254`), and `execute_run` already has an operator-driven
  resume path for `FAILED`/`RETRYING` runs (`orchestrator.py:268`).

---

## 5. Why "no new storage" works for metrics

`_update_alert` (`apps/alerts/services.py:329`) writes an `AlertHistory` row on **every** ingest
cycle — `event="updated"` when nothing changed status. `_DIFF_FIELDS` includes `annotations`,
where the cluster driver stashes `metrics` as JSON.

Consequently:

- one row per checker, per node, per push cycle, timestamped by `created_at`
- `details["changed"]["annotations"]` holds both old and new, so the baseline can be seeded from
  the earliest row rather than needing a separate snapshot
- a row *without* an annotations diff means the metrics were byte-identical to the previous
  sample

So last-value-carry-forward reconstruction is **exact**, not approximate. The cost moves from
storage to query: reconstruction is Python over JSON with no usable index, which is fine for one
alert over a bounded window and bad for a dashboard-wide sweep. Any read path must therefore
carry an explicit window and limit.

Note this is also why Grafana cannot read the metrics directly from the database — it issues SQL
and cannot run the carry-forward. Combined with SQLite (`config/settings.py:109-114`, no WAL
configured), pointing Grafana at the DB would mean a community plugin plus a polling reader on
the same file as the ingest hot path. Hence the notify-sink direction instead.

---

## 6. Resolved: the CHECK-skip invariant lives in the data

The CHECK stage means "run the hub's own checkers." For alerts pushed by a node that is the
wrong machine: the node already ran its checkers, so running them on the hub reports the hub's
CPU and disk, not the node's. The `cluster` driver suppresses the stage for exactly this reason.

**Decision: pure data, no guard.** The lane's stage list is the whole truth. The engine never
special-cases cluster, and `PipelineDefinition` never learns the word. Node lanes simply ship
without CHECK.

Rejected alternatives, and why:

| Option | Why not |
|---|---|
| **Data + model validation** | `clean()` rejecting CHECK on a `source=cluster` lane gives the model layer domain knowledge — the small special case that attracts more (scope-discipline rule 2). |
| **Keep `skip_checkers` on the driver** | Leaves 3.2 exactly as it is; the map still cannot be drawn from `PipelineDefinition` rows alone. |
| **Redefine CHECK** | Changes what CHECK means for every existing lane to remove one invariant. |

The residual risk is a misconfigured lane running hub-side checks against node traffic: useless
but harmless, and plainly visible on the map. The map is the feedback loop.

---

## 7. The design

### 7.1 Data model

`PipelineDefinition` loses three booleans and the M2M, and gains one field:

| Change | From | To |
|---|---|---|
| Stage selection | `run_checkers` / `run_intelligence` / `run_notify` | `stages` — ordered JSON list |
| Channel | `channels` M2M | `channel` FK to `NotificationChannel`, nullable |

`stages` holds **downstream stages only** — a subset of `[CHECK, ANALYZE, NOTIFY]`, and for a
checker-generated lane a subset of `[ANALYZE, NOTIFY]`. It deliberately does not list the entry
stage. A lane is resolved *from* the alert the entry stage produced, so the entry has already
run by the time a lane is known and no lane can control it. Writing it into the row would create
a second field that displays but does nothing — the defect 3.3 exists to remove.

Entry point is instead expressed as a **fact** the lane matches on (§7.2), which is what it
actually is. The map draws the entry node from `match`, not from `stages`.

`clean()` validates that `stages` is a subsequence of the canonical order. That is a data-shape
check with no domain knowledge in it.

**Migration.** A data migration derives `stages` from the existing booleans and sets `channel` to
the first active channel by name — precisely what `executors.py:312` selects today, so no lane
changes behaviour on deploy. Surplus channel rows are discarded; they were never consulted.

### 7.2 Routing input: one alert, never a merged incident

`facts_from_alert(alert)` replaces `facts_from_incident`. Facts come from a single alert with no
merging, so the routing input is always explainable: this alert, from this node, with this
severity, entering this way, matched this lane. Fixes 3.4.

The fact set gains `origin`, read from `PipelineRun.origin` (`models.py:110`, values in
`PipelineOrigin`: `incoming_webhook`, `checker_generated`, `manual`). The field already exists and
is already recorded per run; routing simply ignored it. Lanes can now match on where traffic
entered:

```
match:  [{"field": "origin", "op": "is", "value": "checker_generated"}]
stages: ["analyze", "notify"]
```

`IngestExecutor` also stops using the global `order_by("-received_at")` query. `ProcessingResult`
carries the alerts this call actually touched, and the subject is the highest-severity one among
them, tie-broken by name. This is deterministic and scoped to the push, so concurrent same-source
pushes can no longer steal each other's subject — fixing 3.10(2). It does not fix 3.10(1); see §9.

### 7.3 No hidden default

Delete the `default` list in `_downstream_stages`. A seeded catch-all row — empty `match`, lowest
priority, full stage list — preserves today's behaviour as configuration that an operator can
read, edit, reorder or delete, and that draws on the map like any other lane. `matches()` already
treats an empty `match` as a catch-all and fails closed on malformed conditions
(`models.py:564-588`), so no engine change is required.

If someone deletes the catch-all, an unmatched run ends
`mark_failed("no_route", ..., retryable=False)`: existing status, existing admin surface, existing
resume path once a lane is added. **No quarantine state, no new proxy model, no new admin view** —
the fix for an invisible lane is to make it visible, not to add a second place for traffic to sit.

### 7.4 `skip_checkers` deleted

Removed from `BaseDriver`, `cluster.py`, the webhook wrapper and `_downstream_stages`. Cluster
behaviour is preserved by a seeded lane matching `source=cluster` whose `stages` omit CHECK.
Fixes 3.2.

`checks_only` stays, but only as an entry-point selector (§7.6) — it no longer terminates a run.

### 7.5 One resolution point

Routing resolves once, immediately after the entry stage, stamping `incident.pipeline`.
`NotifyExecutor._route_incident` reads `incident.pipeline.channel` and never re-resolves.
Fixes 3.5.

### 7.6 Both entry points route, under one rule

The unifying rule: **the entry stage produces an alert, the lane is resolved from that alert, the
lane's stages run.** INGEST is the entry for webhook traffic; CHECK is the entry for
checker-generated traffic. Same mechanism, no special case.

Concretely, a `checks_only` run no longer terminates at `CHECKED`. After CHECK, the orchestrator
resolves a lane from the alert `CheckAlertBridge` created, with `origin=checker_generated` among
the facts, and runs that lane's stages. The hub's five-minute self-check can then notify.
Fixes 3.9.

Operators who want a quiet diagnostic run keep `--no-incidents`, which suppresses incident
creation upstream of any of this (`executors.py:131-136`).

### 7.7 `reeval_existing` through orchestration

`apply_node_alert_reeval` opens a `PipelineRun` carrying a `trace_id`; severity changes and
incident resolutions happen inside it and route through the matched lane. **It notifies.** A
config change that closes an incident sends the resolution like any other, restoring the "given a
notification, jump back to the exact incident" guarantee. Fixes 3.8.

This is the most operator-visible change in the design: threshold edits now produce outbound
messages where previously they produced silence.

### 7.8 Readable host in the alerts admin

Add a `host` display column to `AlertAdmin` backed by `instance_key_from_labels`
(`services.py:70-79`), which already falls through `instance_id` → `instance` → `hostname`, with
the `node` FK preferred when set. The server name then shows for every source instead of only
cluster pushes. Reuses the helper that already defines "which host" for incident grouping, so no
second notion of host identity is introduced. Fixes 3.11.

### 7.9 On seeded lanes

The concern that shipping a "checker lane" and a "webhook lane" creates a special case that grows
is right about the risk, and the answer is framing. There are no such concepts in code: one table,
one resolution rule, and an engine that knows nothing about cluster or webhooks. The system ships
with ordinary rows differing only in their `match` and `stages` values — as editable and deletable
as any row added later. Two code paths would grow; rows are default content.

---

## 8. What this buys

Nothing gets faster at runtime; this is not a performance change. What it buys:

- **Routing is visible.** Every path is a row. The hidden Python default is gone.
- **Config stops lying.** The channel field matches what delivery actually does.
- **One place decides.** Whether checkers run is one field, not two modules and a payload key.
- **The hub monitors itself.** Its own scheduled checks stop opening silent incidents.
- **No silent incident closing.** Config-change re-eval is traced and notified.
- **The host is readable.** Server names show for webhook sources, not just cluster pushes.
- **Changing routing stops requiring a deploy.** Edit a row, not code.

No alert, incident or history data is deleted. The only discarded configuration is surplus
`channels` rows, which had no effect.

---

## 9. Deferred: push fan-out (3.10)

The dropped-incident half of 3.10 is a real bug and should be fixed next. It is deferred because
it is **not a routing bug** — it lives in `IngestExecutor`'s choice of subject and in the run/stage
schema. Both candidate fixes are substantial:

| Approach | Cost |
|---|---|
| **Child `PipelineRun` per incident** | Each incident gets its own run, trace, lane and status; every existing per-run mechanism keeps working unchanged. Multiplies `PipelineRun` / `StageExecution` rows per cycle, making retention (3.7) urgent. |
| **Loop stages within one run** | Row counts stay flat, but `StageExecution` needs an incident dimension: `unique_stage_attempt` on `(pipeline_run, stage, attempt)` (`models.py:421-425`) forbids two NOTIFY rows in one run, and `_stage_completed` (`orchestrator.py:684-690`) asks "did NOTIFY succeed?" run-wide, so a resumed run would skip NOTIFY for every incident once any one succeeded. Retry and idempotency become per-incident too. |

Child runs are the structurally cheaper of the two — the entire cost is row volume. That is the
follow-up's decision to make, together with retention.

The follow-up must also answer what a **zero-incident push** does. `NotifyExecutor` runs today
even with no incident, and the parked metrics-egress direction (§2) depends on that running every
cycle including healthy ones. Any "stages run per incident" model must sit on top of a run that
happens regardless, or it silently removes that hook.

---

## 10. Sequencing

1. **Routing simplification** — this document.
2. **Push fan-out + retention** — §9 and 3.7 together, since fan-out drives run volume.
3. **The network map** — drawn against the simplified model. Doing it earlier means encoding the
   fallback lane, the split CHECK gating and the phantom fan-out into the graph builder, then
   rewriting it.
4. **TSDB / metrics egress decision** — parked, independent of the above.
5. **Admin lightening** — gated on 4. Stripping aggregates out of `config/dashboard.py` before
   Grafana serves them would leave that information nowhere. The readiness panel is operational
   and stays regardless.

---

## 11. Still open

- 3.6, node-bound fingerprints — untouched here. Becomes live if "the same alert on five nodes"
  should ever group or route as one thing.
- Retention (3.7) — needs an owner, and becomes urgent once fan-out lands.
