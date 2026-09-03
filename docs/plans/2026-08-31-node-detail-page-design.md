---
title: "Node detail page overview"
parent: Plans
---

{% raw %}
# Node detail page overview

## Problem

Two separate complaints, one page.

**The page is a form, not a view.** `/admin/alerts/node/<id>/change/` renders the
registry fields, an editable `config`, and three readonly extras stacked in
single-column label/value rows: a 120x24 disk sparkline, a list of ten pipeline
runs, and a one-line preflight summary. An operator who opens a node because the
changelist flagged it red learns almost nothing about what is actually wrong with
that machine. The severity chips that carry the signal live on the changelist and
stop at its edge.

**Latest preflight is broken for the hub itself.** `manage.py preflight` persists
`instance_id=settings.INSTANCE_ID` (`apps/checkers/management/commands/preflight.py:73`).
That setting is `""` on a hub that never set the env var, because only agents are
told to set it (`.env.sample:42`). The hub's own `Node` row, however, is keyed by
`apps.alerts.identity.local_instance_id()`, which falls back to
`socket.gethostname()`. `NodeAdmin.latest_preflight` filters
`PreflightRun.objects.filter(instance_id=obj.instance_id)`, matches nothing, and
prints "No preflight recorded." even immediately after a successful run. The run
is not lost: the PreflightRun changelist shows it, because that view does not
filter by node. Two writers, two spellings of the same machine.

## The constraint that shapes the charts

`CheckRun` rows are written in exactly one place, `apps/checkers/checkers/base.py:126`,
by the machine that ran the checker. There is no hub-push for them. So a hub holds
`CheckRun` history for itself and for nobody else.

A peer reaches the hub as `Alert` rows instead: deduped on
`(fingerprint, source)`, updated in place, carrying `metric_*` labels and an
`annotations` dict of metric values. One row per checker per node, overwritten on
every push. Current state, never a series.

The consequence is blunt: today's disk sparkline is permanently empty on every
peer node page, and no amount of admin work changes that. Storing a metric history
for pushed results is a real feature with its own model, migration and retention
policy. It is **deliberately out of scope here** and left as a later project. This
design instead makes the gap legible rather than pretending it does not exist.

## Design

### Page shape

A new `templates/admin/alerts/node/change_form.html` extends
`admin/change_form.html` and overrides `{% block content %}` to render an overview
above `{{ block.super }}`. `NodeAdmin.render_change_form` supplies the context,
built by a new `apps/alerts/node_overview.py`, so panel logic is unit-testable
without driving the admin.

`disk_sparkline`, `recent_pipelines` and `latest_preflight` move out of
`readonly_fields` and `fields` into the overview. The form below is left as the
identity fields plus the one operator-editable field, `config`.

Precedent: `templates/admin/dashboard.html` with `config/dashboard.py`.

### Panel 1 — identity header

Role badge: **This hub** when `node.instance_id == local_instance_id()`, otherwise
**Peer**. Then hostname, address, instance_id, `last_source`, and `last_seen`
rendered as an age with a green/amber/red read.

The freshness thresholds are imported from `config/dashboard.py` rather than
restated, so the dashboard nodes card and this page can never drift apart.

The unresolved-severity chips move out of `NodeAdmin.incidents` into a shared
helper that both the changelist column and this header call, so one node reads the
same on both pages.

### Panel 2 — per-checker current state

One row per checker this node reports: checker name, status, latest metric value,
age, and a link to the underlying record. Two sources, one shape:

- **local node** — the newest `CheckRun` per `checker_name`
- **peer** — the node's `Alert` rows that carry a `labels["checker"]`, newest per
  checker, value read from `annotations`

Both normalise to the same row dataclass, so the template runs one loop and has no
idea which source it drew from. This is the only panel that works for every node,
and it is the answer to "what is actually wrong with this box".

### Panel 3 — recent incidents

The ten newest incidents reached through `alerts__node`, each with severity,
status, age and an admin link. The chips in the header give counts; this gives
names.

### Panel 4 — charts

Three time series from `CheckRun`, larger than today's sparkline:

| Chart | Checker | Metric |
|---|---|---|
| Disk usage | `disk` | `worst_percent` |
| CPU | `cpu` | `cpu_percent` |
| Memory | `memory` | `memory_percent` |

`render_sparkline` (`apps/checkers/admin_charts.py`) grows optional y-axis
min/max labels and a title, with defaults that keep its existing callers
byte-identical.

Charts render **only for the local node**. A peer gets one honest sentence in
their place: metric history is written by the machine that runs the checker and is
not pushed to a hub, so there is nothing to plot here yet. No blank chart, no
empty axes.

### The preflight fix

Three parts:

1. `preflight.py` writes `local_instance_id()` instead of raw
   `settings.INSTANCE_ID`. One import, one line. Now both writers spell the
   machine the same way.
2. A data migration in `apps/checkers` stamps existing `instance_id=""` rows with
   this machine's id, so history written before today becomes visible instead of
   staying orphaned.
3. The preflight panel renders only for the local node. Peers get the same
   node-local explanation as the charts: preflight is node-local and is not pushed
   to a hub, so a hub never holds a peer's preflight run.

Read-time matching on a blank id was rejected: it leaves the bad data in place and
obliges every future reader to know the trick.

## Testing

- `apps/alerts/_tests/test_node_overview.py` — the four panel builders, each over
  local vs peer and empty vs populated, with non-numeric metric values skipped
  rather than raising.
- An admin test asserting the custom template renders and that the preflight panel
  resolves for the hub's own node — the regression that started this.
- `apps/checkers/_tests` — the writer fix, and the migration backfill over a row
  with a blank instance_id.

100% branch coverage on changed code, per `AGENTS.md`.

## Out of scope

- A metric-sample store for pushed results. Named here so the next person knows
  the empty-state text is a deliberate placeholder, not an oversight.
- Preflight retention pruning, already flagged as a follow-up in `preflight.py`.
{% endraw %}
