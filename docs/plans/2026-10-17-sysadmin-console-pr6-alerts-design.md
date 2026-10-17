---
title: "Sysadmin Console PR 6: Alerts Design"
parent: Plans
---

# Sysadmin console PR 6: alerts

Domain slice 6 of `2026-10-15-sysadmin-console-foundation-design.md`. It moves alerts to the
console and cuts `AlertAdmin` and `AlertHistoryAdmin` to stock.

## Decision: alerts are machine state

An alert is one condition on one machine, keyed by `(fingerprint, source)`. Only producers
change its status: a node push, a webhook, `check_health` or a re-evaluate. An incident groups
the open alerts on one `(subject, instance)`, and it's what operators act on.

The admin's "Resolve selected alerts" broke that. It was a bare `queryset.update`, so it wrote no
`AlertHistory`, ran no incident resolution and enqueued nothing. The next push from a node that
still saw the condition flipped it back. That action is deleted, not ported. The foundation
design's line "bulk resolve goes through `IncidentManager`" is superseded by this decision. No
operator alert transition exists anywhere.

Flapping noise, like a CPU alert crossing its threshold every few minutes, is a job for incident
snooze. Snooze comes after the console migration and is out of scope here.

## Pages

### `/console/alerts/`

A read-only search over machine state. Each row shows the `describe_alert` headline, severity,
status, node, last seen and a link to its incident.

Filters are chips, the same as the incident list: status (firing by default, resolved, all),
severity, node, source and since. A `q` box searches name, fingerprint and trace ID, which keeps
the admin's search. A node matches an alert linked by FK, or an unlinked alert whose
`instance_id`, `instance` or `hostname` label names it. That's the same rule the incident list
uses, lifted into `queries/hosts.py` so both lists share it.

### `/console/alerts/<pk>/`

The evidence for one machine state:

- `describe_alert` headline, facts and runbook action.
- Node, source, started, last seen and ended.
- Its incident as a card linking to the console incident page.
- The history timeline from `describe_history`. This replaces `AlertHistoryAdmin` as the place
  to read alert history.
- Re-evaluation status from `describe_reeval`.
- Details, collapsed: fingerprint, trace ID, labels, annotations and raw payload. No identifier
  shows outside Details.

### `/console/alerts/<pk>/reevaluate/`

GET shows the preview: what would change and why, and a warning that a resolved alert whose stored
metrics still breach the policy will reopen. POST applies it through `apply_reeval`, which already
writes history and enqueues the incident run.

The double-submit guard is a digest. The form carries `alert_state_digest(alert)`, a hash of
status, severity and `updated_at` at preview time. `apply_reeval(scope, expected=digest)` raises
`StaleReeval` when the alert no longer matches and writes nothing. The page then says "This alert
changed since you looked" and shows a fresh preview. A replayed POST hits a changed `updated_at`
and does nothing. PR 8 reuses this guard for node-wide re-evaluate.

It needs `alerts.change_alert`, the same as the admin today.

## Wiring

- The `view_alert` intent points at `console:alert`, so the incident page's alert rows link into
  the console.
- The sidebar gains Alerts under Incidents.
- Reads need `alerts.view_alert`, following PR 5.

## Code layout

`apps/alerts` owns the wording:

- `describing.py` gains `describe_reeval(alert)`, which replaces `reeval_display.py`. The clause
  logic moves over. The HTML panel and its module are deleted, because the admin was its only
  consumer.
- `describing.py` gains `describe_reeval_report(report)` for the preview, taking over
  `alert_reeval_intro` from `admin.py`.
- `reeval_existing.py` gains `alert_state_digest(alert)`, `StaleReeval`, and an `expected`
  argument on `apply_reeval`. The guard lives next to the write, not in the view.

`apps/console` owns reading and routing:

- `filters.py`: `AlertFilter`, sharing parsing helpers with `IncidentFilter`.
- `queries/alerts.py`: `build_alert_list`.
- `queries/alert.py`: `build_alert_detail` and `build_reeval_preview`.
- `views/alerts.py`, `views/alert.py`, `views/alert_reeval.py`, and their templates and partials
  following the incident ones.

## Admin cut

- `AlertAdmin` and `AlertHistoryAdmin` become stock, read-only `ModelAdmin`s. The custom
  displays, the per-alert reevaluate object action and `resolve_selected` are removed.
- `IncidentAdmin` stays custom until PR 7. Its run inline and journey are the only way from an
  incident to its runs until the runs pages exist.
- `NodeAdmin`'s node-wide reevaluate keeps `reevaluate_confirm.html` until PR 8.

## Tests

- Queries: every filter, node match for linked and unlinked alerts, pagination, and the
  dropped-param notice.
- Views: view permission for reads, change permission for re-evaluate, 403 and 404. Preview GET
  writes nothing. POST applies, writes history and enqueues the incident run. A stale digest
  refuses and writes nothing. A replayed POST is a no-op.
- `test_guard.py` covers the alert pages: no UUID, `snake_case` token or `{` outside Details.
- Admin: `AlertAdmin` and `AlertHistoryAdmin` are stock and read-only, and `resolve_selected` is
  gone.
- 100% branch coverage on changed files.

## Docs

- `apps/alerts/AGENTS.md`: alerts are machine state, with no operator alert transitions.
- `apps/console/AGENTS.md`: the alert pages and the digest guard.

## Out of scope

- Incident snooze.
- `IncidentAdmin`, runs and node pages.
- The refire episode decision.
