---
title: "Node changelist incidents overview"
parent: Plans
---

# Node changelist incidents overview

## Problem

`/admin/alerts/node/` lists the fleet as a registry: instance_id, hostname,
last_source, first_seen, last_seen. It answers "is this agent still reporting"
and nothing else. To learn whether a node is actually in trouble an operator
leaves the page, opens the incident changelist and filters by node, one node at
a time. The one screen that shows every machine at once says nothing about the
state of any of them.

## Design

### The column

A single `incidents` column on `NodeAdmin.list_display`, placed after
`hostname`. It renders the node's unresolved incident counts split by severity,
worst first, each part its own link:

```
instance_id   hostname   Incidents                 last_seen
node-a        web-01     2 CRITICAL · 1 WARNING    2 min ago
node-b        web-02     —                         1 min ago
```

A node with nothing unresolved renders an em dash, not a zero. Quiet nodes
should read as quiet.

Each severity links to the incident changelist already filtered to that node and
severity:

```
/admin/alerts/incident/?alerts__node__id__exact=<id>
    &status__in=open,acknowledged
    &severity__exact=critical
```

That reuses the `alerts__node` list filter and the `.distinct()` queryset added
for the incident Host column, rather than adding a parallel view. Badge colors
come from the same map `AlertAdmin.severity_badge` uses, so severity reads the
same wherever it appears.

### Unresolved

Unresolved means `open` or `acknowledged`. An acknowledged incident is a live
problem someone has picked up, so hiding it would make a node look healthy the
moment an operator touched it. `resolved` and `closed` are excluded.

### Counting

The counts are annotations on the changelist queryset, one conditional
aggregate per severity:

```python
Count(
    "alerts__incident",
    distinct=True,
    filter=Q(
        alerts__incident__status__in=UNRESOLVED_INCIDENT_STATUSES,
        alerts__incident__severity=severity,
    ),
)
```

`distinct=True` carries the weight. A node with six alerts rolled into one
incident is one incident, not six. All three aggregates ride the same
`alerts__incident` join, so this stays one query no matter how many nodes are
listed. No per-row lookups.

A fourth annotation totals the unresolved count and backs `admin_order_field`,
so the fleet can be sorted by how bad it is.

## Rejected

**A latest-incident-title column.** Showing the newest unresolved incident's
title next to the counts saves a click, but it costs a per-row query and the
counts already answer the triage question. Skipped.

**Computing in Python over a prefetch.** Simpler to read, but it pulls every
alert and incident row for every listed node to produce three integers.

## Tests

- Zero unresolved incidents renders an em dash.
- Several alerts on one incident count as one incident.
- `resolved` and `closed` are excluded; `acknowledged` is included.
- Another node's incidents do not leak into this node's counts.
- The rendered link filters the incident changelist to that node and severity.
- Ordering by the column sorts by total unresolved count.
- Severity output is escaped; the column returns no attacker-controlled markup.
