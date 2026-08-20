---
title: "Incident Fan-out and the Change Gate"
parent: Plans
---

# Incident fan-out and the change gate

**Status:** design approved 2026-08-19. Follows the routing simplification
(`docs/plans/2026-08-12-routing-simplification-design.md`, merged as PR #207), which deferred
this work as §9 / 3.10.

**Why now:** §10 sequences this before the network map. A graph builder written against today's
model would encode "one push routes one incident" and have to unlearn it.

---

## 1. The problem

`IngestExecutor` opens every incident in a push, then collapses the batch to one subject
(`apps/orchestration/executors.py:91`, `apps/orchestration/routing.py:13-30`). Everything after
the entry stage — lane resolution, ANALYZE, NOTIFY — runs against that single `incident_id`.

A node with three firing checkers therefore produces three incidents, of which **two are
diagnosed and then silently dropped**. They exist in the database, they are counted in
`incidents_created`, and nothing else ever happens to them. No lane, no analysis, no message.

This is a bug, not a design choice. It is also unrepresentable on a map: the collapse lives in
Python, not in `PipelineDefinition` rows, so no amount of reading lane data reveals it.

## 2. The shape

**The unit of work becomes the incident event, not the push.**

1. **The push run is unchanged.** A webhook or node push is claimed by `process_inbox` and runs
   INGEST — or CHECK for checker-origin traffic. `AlertOrchestrator` opens or updates all N
   incidents exactly as it does today. This run always exists, including for a healthy push with
   zero incidents, which preserves the metrics-egress hook described in §2 of the routing design.

2. **Each incident that passes the gate gets its own downstream run**, carrying that one incident
   as its subject. `ctx.incident_id` keeps its present meaning. The run resolves **its own lane**
   from its own incident's facts, runs the stages that lane lists, and notifies that lane's
   channel.

3. **Downstream runs are started via the inbox**, recorded as `PENDING` `PipelineRun` rows and
   drained by `process_inbox` like any other work. This reuses the Phase C machinery wholesale:
   durability, atomic claim, stale-reclaim, `INBOX_DEPTH_WARN` backpressure, and per-incident
   retry. The alternative — looping inline inside the push run — would hold one run open across
   N analyses, and a crash mid-loop would lose all of them.

   (Corrected during implementation: an earlier draft said the inline alternative "would execute
   N LLM calls in one drain tick, which is the memory-pressure shape Phase C exists to prevent",
   implying the inbox prevents that. It does not. `inbox.drain()` snapshots pending PKs up front,
   so children wait for the *next* pass — but that pass claims all of them if `--limit` allows,
   and runs them sequentially. What the inbox actually buys is that N analyses become N
   independently claimed, independently retryable, crash-isolated runs, bounded by `--limit`
   rather than by the payload.)

4. **Correlation is free.** Downstream runs inherit the push's `trace_id` and get their own
   `run_id`. Both fields exist (`apps/orchestration/models.py:65-70`), indexed as a pair
   (`:223`), and `manage.py trace` already projects the journey over `trace_id`. One push still
   reads as one story. No parent FK.

5. **`subject_alert()` survives** as the helper that picks a run's headline, but it stops deciding
   who gets analysed and who gets dropped.

### What this costs in the run schema

Nothing. Because each downstream run carries exactly one incident, `StageExecution`'s identity
`(pipeline_run, stage, attempt)` (`models.py:421-425`) stays correct, `_stage_completed`
(`orchestrator.py:403`) stays correct run-wide, and the channel-scoped idempotency key
(`executors.py:404`) stays correct. Retries, resume, signals and the audit trail all keep working
untouched.

One orchestrator change is required: a downstream run must execute with **no entry stage at
all**, since it does not re-ingest. (Corrected during implementation: an earlier draft said
"ANALYZE must be accepted as an entry stage". Treating ANALYZE as an entry stage would force it
to run — and a resolved incident routes to a lane listing only `notify`, so that would call the
AI on an all-clear. The run instead resolves its lane from the incident it was handed and runs
exactly what that lane lists, which may be nothing.)

## 3. The gate

Fan-out removes an accidental rate limiter. Today one run analyses one incident; afterwards it
would analyse N, on every cron tick, with no cooldown anywhere in `apps/notify` or
`apps/orchestration`. For one node with three chronic incidents on a five-minute cron that is 864
LLM calls a day instead of 288 — free on the `local` provider, a real bill on `claude`, `openai`,
`gemini`, `mistral`, `grok` or `copilot`.

**A downstream run starts when any of these hold:**

1. The incident was **created** by this push.
2. Its **status** transitioned (firing→resolved or resolved→firing).
3. Its **severity** changed in either direction.
4. The checker's **context key** changed since the last downstream run.

Everything else — the steady-state re-push of an unchanged incident — starts no run at all. No
lane resolution, no LLM call, no message. This also closes the 288/day re-notify gap as a side
effect.

### The gate must not read `AlertHistory` events

The two ingest paths write different events. `AlertOrchestrator._update_alert`
(`apps/alerts/services.py:322-370`) writes `refired`/`updated` with a `_diff_alert` payload;
`CheckAlertBridge._update_alert` (`apps/alerts/check_integration.py:309-345`) writes only
`severity_changed`. A gate built on history rows would behave differently for webhook and checker
traffic — and `listening_ports`, the case that motivated the gate, is checker traffic. The
predicate is therefore evaluated once, over incident state, for both paths.

### The gate must not read free text

`_diff_alert` compares `description` (`services.py:310`), and for checker alerts
`description = result.message` (`check_integration.py:158`) — text carrying live metric values.
That string changes on nearly every tick, so a diff-based gate would suppress almost nothing.

### The context key

A **hub-side registry keyed by the `checker` label** (`apps/alerts/context_keys.py`) maps a
checker to a builder returning a stable string describing *what situation this alert is about*.
A checker with no entry has no key, meaning severity and status alone decide, so no existing
checker changes behaviour.

(Corrected during implementation: an earlier draft made this an optional method on
`BaseChecker`. It cannot be — checkers run on **nodes**, while the gate runs on the **hub** over
`Alert` rows, so the hub never holds the checker object. The registry mirrors
`apps.alerts.reevaluation`'s `SCORERS`/`REEVALUATORS` dicts, reads the metrics both producers
already write into annotations, and therefore requires **no node redeploy**.)

- `listening_ports` returns its sorted port set, read from `metrics` — the same source
  `_score_allowlist` already reads (`apps/alerts/reevaluation.py:120-144`). A new
  non-allowlisted port therefore notifies even though severity is unchanged.
- `cpu`, `memory` and the other metric checkers have no entry, so percentage jitter can never
  defeat the gate.

The previous key is stored in a new nullable `context_key` CharField on `Alert`, giving an O(1)
comparison and a value visible in admin beside `fingerprint`. **This is the only migration in
this design.**

### Already handled upstream: allowlisted ports

A fully-allowlisted `listening_ports` result is re-evaluated to `("info", "resolved", count)`
(`reevaluation.py:120-144`), and incidents are auto-created only for `CRITICAL` or `WARNING`
(`check_integration.py:301-305`). Such an alert never becomes an incident and never reaches the
loop. The gate's target is the genuinely non-allowlisted port that keeps firing unchanged.

## 4. Resolution notifies without analysing — as data

An all-clear should reach the operator, and running the AI on it is pointless. That divergence
needs no code: it is a lane.

```
match:  [{"field": "status", "op": "is", "value": "resolved"}]
stages: ["notify"]
```

The routing spine already expresses it, an operator can read and edit it, and it draws on the map
like any other lane.

## 5. Alternatives considered and rejected

| Alternative | Why not |
|---|---|
| **Loop stages inside one run**, with a `subject_key` dimension on `StageExecution` | Needs a column, a unique-constraint swap and a changed `_stage_completed`, purely to let one run represent N subjects. Per-incident runs get the same result with the existing schema. |
| **Nullable `incident` FK on `StageExecution`** | Grouped rows store NULL, and SQLite (`config/settings.py:111`) treats NULLs as distinct, so the constraint stops protecting the stage it was added for. `UniqueConstraint(nulls_distinct=False)` is PostgreSQL-only. |
| **N results inside `output_snapshot`** | Zero migration, but partial-failure retry must re-read JSON to avoid re-sending to already-delivered channels. Behaviour that is not data — the defect class the routing simplification removed. |
| **Aggregate one AI analysis per node** | Rejected by the operator: unrelated incidents (high memory, a pending Debian reboot) do not form one story. A bundled whole-node analysis becomes an *operator-triggered* action on the roadmap instead, which is where on-demand correlation belongs. |
| **Snooze instead of the gate** | Snooze answers "I know about this one" (operator intent, per incident); the gate answers "nothing has changed" (systemic, automatic). The 288/day problem is systemic, so the gate ships first. Snooze is a follow-up. |

## 6. Blast radius

- **Migration:** one — `Alert.context_key`.
- **Changed:** `IngestExecutor`/`CheckExecutor` subject handling, orchestrator handling for a
  run with no entry stage, the new gate module, the hub-side context-key registry, and a seeded
  `resolved` lane.
- **Untouched:** `StageExecution` schema and constraints, retry logic, idempotency keys, the
  signal set, `PipelineDefinition`, and the inbox drain itself.

## 7. Acceptance

- A push with three firing incidents produces three downstream runs resolving three lanes.
- The same push repeated with nothing changed produces none.
- A severity escalation on one incident produces exactly one downstream run.
- A resolve produces one run that notifies without analysing.
- A zero-incident push still produces its parent run.
- A `listening_ports` alert gaining a new non-allowlisted port at unchanged severity produces a
  run; the same port set repeated does not.
- 100% branch coverage on changed code; `black`, `ruff`, `pytest`, `pip-audit` and `bandit` green.

## 8. Out of scope, and what still stands between here and the map

Deliberately excluded, each independently shippable:

1. **The `NotifySelector` fallback.** When a lane's channel is unset or inactive,
   `routed_channel()` returns None and `NotifySelector.resolve` picks the first active channel by
   name (`apps/notify/services.py:66-78`). That is the same structural defect as the routing
   fallback deleted in #207: delivery decided in Python, not data. Binding channels via
   `setup_cluster` or admin makes today's map honest; deleting the fallback makes it permanently
   honest. **Do this before drawing anything.**
2. **Shadowing.** First-match-wins is a property of the ordered set, not of any lane, so lanes can
   be unreachable — computable from the data, but the map must decide whether to draw dead lanes
   as dead, or show paths that do not exist.
3. **Snooze** — `snoozed_until` plus a reason on `Incident`, admin presets, and severity-escalation
   break-through so a snoozed warning that turns critical still notifies.
4. **Retention (3.7)** — less urgent under the gate than §9 assumed, since run volume now tracks
   real events rather than cron ticks, but still unowned.
5. **The map itself.** Lanes draw as nodes labelled with their match expression; the input space
   is not enumerable, because `match` holds arbitrary predicates.

Sequencing: this design → the notify fallback → shadowing decision → the map.
