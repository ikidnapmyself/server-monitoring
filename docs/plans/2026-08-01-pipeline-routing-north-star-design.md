---
title: "Pipeline Routing — North-Star Architecture"
parent: Plans
---

# Pipeline Routing — North-Star Architecture

**Status:** Design (north star), written and approved 2026-08-01 as the target
architecture. Deliberately a **plan to build toward**, not a big-bang build —
each phase (A–D) ships independently and non-breaking.

Builds on the peer-node architecture (`docs/plans/2026-07-26-node-model-control-plane-design.md`,
fully merged). This doc closes the last structural gap that earlier phases exposed.

## Problem — there is no routing anywhere

Every "which alert → which behaviour → which channel" decision today is either
hardcoded or "first one wins":

- **Notify** picks *the first active channel, alphabetically* (`NotifySelector`).
  `NotificationChannel` has no match fields.
- **Pipeline shape** is hardcoded (`PipelineOrchestrator`), with the
  `skip_checkers` **driver flag** as the only routing-ish decision — a one-off hack.
- `PipelineDefinition` (a legacy node/edge **graph** model) and its
  `DefinitionBasedOrchestrator` sit unused **by the default webhook pipeline path**
  (they remain reachable via the definition validate/execute endpoints and
  `run_pipeline --definition …`) — parallel to the real path, not driving it.

That single absence forced the `skip_checkers` hack, the "first active channel"
crudeness, and the ad-hoc `{driver}-primary` channel created by guided setup. It
is the root cause behind "I add features but keep needing to refactor."

## The primitive: reshape `PipelineDefinition` → a flat `Pipeline`

**No new `Route` model.** We reshape the existing (legacy) `PipelineDefinition`
graph into one flat model that *is* the route:

```
Pipeline
  # match (all optional; a condition is {field, op, value})
  match:      list[{field: origin|severity|label:<k>|instance, op: is|is-not|in|not-in, value}]
  priority:   int          # lower evaluated first; seeded by specificity, editable
  # behaviour (plain flags — no graph)
  run_checkers, run_intelligence, run_notify: bool
  # target
  channels:   M2M -> NotificationChannel
  is_active:  bool
```

`origin` ∈ `self | <Node> | <source>`. `Alert`/`Incident` gain an FK to the
`Pipeline` that handled them; a `Node` (or self) binds to its pipeline. This is
one model doing match + behaviour + target — not `Route` **and**
`PipelineDefinition`. "Retire the graph" and "add routing" become the *same*
change.

### Why flags, not a graph
The flags give all the flexibility real cases need, without a node/edge engine:
- **Data-protection inbox:** all flags `false` → record the alert, no processing.
- **Grafana-on-hub → Claude:** `match: source=grafana`, `run_intelligence=true`.
- **Cluster push:** `run_checkers=false` → replaces the `skip_checkers` hack.

## Resolution: explicit priority + first-match-wins + negation

**Not implicit specificity.** Specificity ordering is ambiguous (is
`origin+severity` more specific than `origin+labels`? — an unanswerable tie) and
not debuggable. Instead:

> Walk `Pipeline`s by `priority`; the **first match wins** (short-circuit). No
> match → **inbox** (record only, no stages).

- `priority` is **seeded from specificity** (more conditions ⇒ earlier) so
  "specific beats generic" out of the box — but stored **explicitly**, so ties are
  a visible, editable number, not a hidden rule.
- **Negation is first-class:** a condition's `op` may be `is-not`/`not-in`, so
  *"all critical **except** source=noisy"* is one rule. And because it's
  first-match-wins, an **exception is just a higher-priority rule** — rules and
  exceptions are one primitive at different priorities.
- **Debuggability is the whole point:** the resolver **stamps the matched
  `Pipeline` on the `Incident`** and logs `matched pipeline #7 (priority 20)`. That
  same stamp powers the journey view (below).

**Out of v1 (YAGNI):** Alertmanager-style `continue: true` fan-out across multiple
rules. First-match-wins + *multiple channels per rule* already covers fan-out;
cross-rule continuation adds dedup/loop concerns with no current need.

**Benefit:** predictable, debuggable, exceptions for free.
**Limit:** one pipeline handles a given incident (no cross-rule fan-out in v1);
fan-out is per-rule via multiple channels.

## Orchestration blast radius — four seams, engine preserved

The engine barely moves. **Unchanged:** the four executors
(`Ingest/Check/Analyze/Notify`), the run loop + retries/backoff + signals +
correlation IDs, and the `PipelineRun`/`StageExecution` audit models.

**Changes (the seams):**
1. **New resolver step** — after `INGEST` (which yields source/severity/labels),
   resolve the matched `Pipeline`.
2. **Stage selection** (`orchestrator.py` ~263–272) reads the matched `Pipeline`'s
   flags instead of `payload.checks_only/skip_checkers`. Payload flags stay as an
   override for CLI (`--checks-only`, `--sample`) and migration back-compat.
3. **Notify target** (`executors.py` ~307–347) uses the matched `Pipeline`'s
   channels; `NotifySelector` "first active" remains the fallback.
4. **Trigger/queueing** — durable ingest (below): *how a run starts*, not the run
   logic.

**Sequencing nuance (the one real engine edit):** today `active_stages` is chosen
*up front*. Routing needs ingest output first, so the flow becomes **INGEST always
runs → resolve `Pipeline` → select the remaining stages from its flags**; inbox =
INGEST only, then stop. A small restructure of *when* selection happens, not new
machinery.

## Durable ingest + inbox + drain (the OOM fix)

**Current risk:** Celery is effectively unused on the nodes, so the webhook path
either enqueues to a Redis nobody drains, or runs the pipeline **inline in the
gunicorn worker** — a flood ties up workers and can **OOM the node**.

> **Decouple ingest from processing.** The webhook **durably records the raw
> inbound alert and returns 202 immediately**; a **drain worker** processes
> pipelines at a controlled rate. The worker is Celery *if present*, else a
> `manage.py process_inbox` loop under **systemd/cron** — no mandatory Redis/Celery
> (the optionality contract). Backpressure = **queue depth**, surfaced by `doctor`.

The **inbox** (unprocessed records), the **drain worker**, and **manual "process
now"** are one mechanism at different triggers.

**Benefit:** floods grow a bounded queue instead of OOM-ing; self-hostable on a
cheap VPS with no broker.
**Limit:** processing is now eventually-consistent (a visible queue delay under
load), not synchronous.

## Observability: the "journey" is a projection, not a model

An alert's lifecycle already exists in data, linked by `trace_id`/`run_id`:

```
Alert → Incident → PipelineRun (trace_id/run_id) → StageExecution[ingest,check,analyze,notify]
```

So the journey is a **read-only filtered join**, no new table:
- **Admin shortcut:** a "Journey" panel/link on the `Alert`/`Incident` page showing
  the `PipelineRun` + `StageExecution`s (stage, status, duration, output ref) **and
  the matched `Pipeline` + why it routed** (the same stamp from resolution).
- **CLI shortcut:** `manage.py trace <alert-id | trace_id>` → the same chain,
  ending in "handled by pipeline #7" or "**inbox — not processed**".
- **Manual process** for an unhandled alert: an admin action **"Process now"**
  (auto-route or pick a `Pipeline`) and `manage.py process_inbox --id …` — the
  inbox escape hatch, same drain mechanism.

**Benefit:** full lifecycle of any alert, one click / one command, zero new tables.
**Limit:** only as complete as the `trace_id` chain — an alert created **outside**
a pipeline run has no journey. So **checker-originated alerts must attach the run's
`trace_id` at creation** (a small task, called out below).

## Phased, non-breaking path

- **Phase A — `Pipeline` spine + resolver; notify uses it.** Reshape
  `PipelineDefinition` → flat `Pipeline`; add the resolver; `NotifyExecutor` uses
  the matched pipeline's channels (fallback = today's "first active"). Guided setup
  writes a `Pipeline` (default `* → your channel`), not a bare channel. Ship a
  default catch-all replicating today's behaviour so nothing breaks.
- **Phase B — pipeline shape from `Pipeline` flags.** Stage selection from
  `run_*` flags (retire `skip_checkers`, keep it working during migration);
  `Alert`/`Incident` FK → `Pipeline`; `Node`-linked results; ensure every alert
  gets a `trace_id`.
- **Phase C — durable ingest + inbox + drain worker** (OOM fix), Celery-optional;
  includes a task to **verify the current Celery/inline behaviour** first.
- **Phase D — retire `DefinitionBasedOrchestrator`**; the **journey** admin panel +
  `trace` CLI; **report read model** over `Node`/`Pipeline`/incidents (the API
  prerequisite you named).

Each phase is independently shippable and leaves the system working. After A,
every new feature (driver, match field, report view) is a **clean addition** to
`Pipeline`/`Channel`/`Node` — not a refactor. That is the point of this doc.

## Explicitly out of scope (drawing the line)

- Cross-rule fan-out / `continue` chains (dedup/loops) — until a real need.
- Multi-tier alert forwarding between hubs.
- A node/edge pipeline *graph* — the flat flags replace it.
- A web setup UI — the surfaces stay CLI + admin.
- Per-alert AI cost controls, rate-limited routing — later, if needed.

## Net model delta

Beyond reshaping the legacy `PipelineDefinition`: **one** new FK (`Incident.pipeline`),
a `Node` link, and a `Pipeline`↔`Channel` M2M. **Zero** net-new top-level models —
the routing, the journey, and the inbox all reuse existing records.
