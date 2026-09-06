---
title: "Re-evaluation as a surface"
parent: Plans
---

# Re-evaluation as a surface

**Status:** designed 2026-09-06, not yet implemented.

Builds on the policy overview page (`docs/plans/2026-09-03-policy-overview-design.md`),
which is on `feat/policy-overview-page` and not yet merged. This design reshapes
that page rather than adding a second one.

## Problem

Three complaints, one cause.

1. The re-evaluation list is not a page. It exists only inside a per-node
   `django_object_actions` preview, reachable after finding the node.
2. A firing alert cannot be re-evaluated. The only scope is a whole node.
3. Whether a re-evaluation applied is invisible. The audit is a JSON string in
   `annotations`, and the config-change path writes `AlertHistory` rows that a
   plain operator resolve is indistinguishable from.

The cause is that the policy overview shows **configuration and never
consequence**. It answers "what did I write?" and never "is it doing anything?"

## Non-goals

- **The hub never fetches from a node.** Nodes push; the hub re-scores what it
  was told. Nothing here reaches out to a machine.
- **No incident-level re-evaluation button.** An incident still resolves as a
  consequence when its last firing alert re-scores to resolved. It gets no
  button of its own.
- **No snooze.** Resolve under a wobbling alert reopening the incident is a real
  gap, recorded below, and belongs to its own slice.
- **No new store.** `AlertHistory` and the two annotation keys already record
  every re-evaluation.

## Approach

Widen `/admin/policy/` into the re-evaluation surface, and generalise the apply
function so it can be scoped. The alternative, a second page at
`/admin/reevaluation/`, was rejected: two pages both listing node-and-checker
rows drift, and the policy page is where an operator already looks.

## The scorer contract

The root of the opacity is that `_score_numeric` collapses six distinct failures
into one `None`: config is not a mapping, a threshold is missing, thresholds are
inverted, the checker has no primary metric, the metrics blob is absent, the
value is not a number. `_score_alert` adds two more, and
`preview_node_alert_reeval` a ninth when the re-score equals the current
severity. Nine reasons, one silence.

So `apps/alerts/reevaluation.py` changes its scorer contract:

```
SCORERS[checker](checker, metrics, cfg) -> Verdict | Skip
```

`Verdict(severity, status, value)` is today's tuple. `Skip(reason, **context)`
carries one enumerated reason plus the values its sentence needs.

**Ingest behaviour is unchanged.** `reevaluate_severity` treats any `Skip` as
passthrough, exactly as it treats `None` today, so re-evaluation stays fail-open.
This matters more than anything else in this design: a scorer raising into
`AlertOrchestrator._process_alert` rolls back an entire webhook batch. The
reasons surface only where a human asked a question.

This change lands **first and alone**, with its fail-open tests, before any page
work.

## Scoping the apply

`apps/alerts/reeval_existing.py` gains:

- `ReevalScope` — a node plus an alert queryset, with three constructors: the
  whole node, one node and checker, one alert.
- `preview_reeval(scope)` and `apply_reeval(scope)`, holding the logic that is
  there today.

`preview_node_alert_reeval` and `apply_node_alert_reeval` become thin wrappers,
so the Node change-page button, the `reevaluate_node_alerts` command and the
post-save redirect into the preview keep working untouched.

`ReevalReport` grows a `skips` list beside `changes`, so a preview that changes
nothing explains itself instead of printing "No open alerts need re-evaluation."

Alerts are matched by their `instance_id` label rather than the `node` FK,
unchanged from today: the FK is stamped only at alert creation, so an alert
created before its node registered is unlinked yet still belongs to the node.

## The page

### The spine widens

A row exists when the node has a config entry for that checker **or** has a
firing alert for that checker. Today only the first produces a row, which is why
a machine alerting on `disk` with no policy at all looks identical to a machine
with nothing wrong.

A checker with no scorer gets a muted, un-actionable state rather than being
presented as a gap an operator can close. That is the memo's biggest pain: the
silent skip that took a diagnostic script to explain.

### Three new facts per row

**Effect** — `3 firing, 2 would change`. The same scorer the preview already
runs, scoped narrower.

**Applied** — `Last changed a severity 4 Aug 14:02`, from `AlertHistory`. A row
can also be in effect at ingest with nothing in history, because
`reevaluate_severity` annotates only when it changed something, so the current
firing alerts' `annotations["severity_reevaluated"]` counts too. "Applied" means
this rule has visibly changed something, by either path.

**Re-evaluate** — a button beside Edit, scoped to that node and checker, going to
the same confirm-then-apply preview. It never applies on the click.

The node-wide button on the Node change page stays, including the post-save
redirect into it. A threshold save usually wants the node-wide sweep.

### One honest label

The preview scores the **last reported value**. The hub never pulls, so a node
that stopped reporting still yields a confident preview built on hours-old
numbers. Every printed value carries its alert's `received_at`, or the page reads
as live when it is not.

### Query shape

The new columns come from one pass: every firing alert on the hub, scored once,
grouped by `instance_id` and checker in memory. Bounded by open alerts, not by
nodes. It must not become a query per row.

## The Alert page

`AlertAdmin` gains a **Re-evaluate** change action, the same preview-then-confirm
scoped to that alert. This is the discoverability complaint: the operator looked
for it on the alert and it was on the node.

Most of the work is in the refusals, one sentence per `Skip` reason:

- `disk_temp is not re-evaluatable — no scorer knows it.`
- `This alert carries no metrics, so there is nothing to re-score.`
- `No policy set for cpu on fiyat-ekrani.` plus a link to those boxes.
- `Policy already matches: cpu is at 41.2, warning starts at 99.`

The last one needs the current value printed next to the threshold, which the
preview already computes and no admin surface shows today.

The page also gains a read-only **Re-evaluation** panel: the two annotation keys
rendered as sentences rather than JSON buried in the collapsed Metadata
fieldset. `CRITICAL to WARNING on 4 Aug, value 91.3, policy warning 90 /
critical 99.`

The incident timeline entry gains the severity change in its detail, so
`reevaluated` stops being a bare word.

## Applying announces

`apply_node_alert_reeval` today commits in the request's own transaction and
tells nobody. It never calls `enqueue_for`, so no `PipelineRun` exists and the
incident's journey has a hole where the re-evaluation was. An operator resolving
an incident by hand does enqueue a manual run, so the same end state reached two
ways produces a run one way and silence the other.

An incident that changes state announces itself regardless of who changed it. So:

- **One `MANUAL`-origin run per incident the apply actually changed**, status or
  severity. A no-op apply enqueues nothing.
- `IncidentManager._announce` is lifted out into a shared function in
  `apps/alerts/services.py`, so there is one implementation rather than a copy.
- Enqueued inside the transaction, so the runs commit with the writes that
  justify them.
- **Async, not `sync=True`.** The admin request must not sit through an analysis
  and a notification round-trip. Operator transitions already do not.

A severity-only change announces too. Suppressing it would mean inventing a
second materiality policy here, next to the one `apps.orchestration` owns, and
that is how the two drift.

**The confirm page states how many runs it will create.** A node-wide sweep
clearing twelve incidents enqueues twelve runs. That is the same shape as a node
pushing twelve changed incidents, so it is within the existing topology, but
`apps.notify` has no cooldown of its own, so the confirm count is the only brake.

## What this design cannot fix

Named here because the page will make them loud.

**Re-evaluation reaches only what the alert stored.** `parse_metrics` reads
`annotations["metrics"]`, which only the cluster driver preserves. Alerts from
the other drivers carry no metrics and can never be re-evaluated, whatever policy
is written.

**`SCORERS` covers seven numeric checkers and `listening_ports`.** `network`,
`process`, `raid`, `reboot_debian` and per-mount disk are not re-evaluatable at
all.

So the most common row on the first render may be "you cannot act on this". That
is the argument for the page rather than against it: today that ceiling is
invisible and gets rediscovered one diagnostic script at a time. The work it
points at next is preserving metrics on more drivers, or adding scorers, not
more UI.

**Resolve does not hold under a wobbling alert.** `follow_alert` reopens a
RESOLVED or CLOSED incident on any material change while the alert still fires,
including a de-escalation. ACKNOWLEDGED absorbs both refires and de-escalations,
so acknowledge is the durable tool, but it leaves the incident on the board.
This bites exactly the checkers that have no scorer, where resolve is the only
tool. It is an incident-lifecycle question, not a re-evaluation one, and belongs
to its own slice: whether RESOLVED should absorb de-escalations the way
ACKNOWLEDGED does, or whether resolve needs an explicit duration.

## Testing

100% branch coverage on changed lines, per `AGENTS.md`. The surface is mostly
branches:

- One test per `Skip` reason, asserting the sentence rather than the enum.
- Ingest fail-open is unchanged under the new contract. This is the regression
  that would hurt most.
- The scoped constructors: a node's scope and the sum of its checker scopes reach
  the same alerts.
- Apply announces one run per changed incident, none for a no-op, and the runs
  commit with the writes rather than after them.
- The widened spine: a firing alert with no config produces a row, a checker with
  no scorer produces a muted row, and a node with neither still only counts
  toward the quiet line.
- `received_at` is printed with every value.

## Acceptance

- `/admin/policy/` shows, for every node and checker that has policy or a firing
  alert, what the policy is, how many alerts it governs, how many would change,
  when it last changed something, and a way to act.
- An operator can re-evaluate one alert from the alert page, and is told in a
  sentence when it cannot be re-evaluated and why.
- An applied re-evaluation is visible on the alert, on the incident timeline, and
  as a pipeline run.
