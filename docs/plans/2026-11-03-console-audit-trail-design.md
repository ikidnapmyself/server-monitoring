---
title: "Console Audit Trail Design"
parent: Plans
---

# Console audit trail

Follows the console migration (#273). `django.contrib.admin` is uninstalled, so its
`admin_logentry` log stopped. Nothing records who changed what. Today only incident
acknowledge and resolve keep an actor, in `Incident.metadata`, overwritten by the next one.

Decisions the user made:

- Every console write is recorded: config, users, keys, and operator actions.
- An entry records which fields changed, old and new. Secrets record only that they changed.
- Operators read it on a console page under System.
- One PR.

## What exists

- 20 console views write: `alert_reeval`, `channel`, `channel_delete`, `inbox`,
  `incident_action`, `incident_rerun`, `key`, `key_delete`, `node_allow_ports`,
  `node_policy`, `node_reeval`, `provider`, `provider_delete`, `route`, `route_delete`,
  `run`, `user`, `user_delete`, `user_password`, `user_promote`. Most do the write in
  `apps/console/queries/` or an app service.
- `config/client_ip.py` resolves the client address behind trusted proxies.
- `AlertHistory` records machine events on alerts. It isn't an operator log and stays as is.
- `apps/console` has no models yet.

## Approach

Each write records its own entry, explicitly, inside the same transaction as the write. A
rolled-back write leaves no entry. Rejected:

- Model `post_save` signals with a middleware that remembers the request user. Operator
  actions (re-evaluate, rerun, retry, drain) aren't one model save, so signals can't word them.
- django-simple-history or django-auditlog. A new dependency and a history table per model,
  and the actor plumbing is still ours.

## Model

`apps/console/models.py`, `AuditEntry`:

| Field | |
|---|---|
| `at` | `DateTimeField(auto_now_add, db_index)` |
| `actor` | FK to the user, `null=True`, `on_delete=SET_NULL` |
| `actor_name` | the username at write time, so a deleted user's entries keep a name |
| `action` | a key from the registry, e.g. `route.edit` |
| `target_kind`, `target_id`, `target_name` | what was written, captured at write time. `target_id` is null when there's no row (a failed sign-in) |
| `changes` | JSON, `{field: {"old": x, "new": y}}`. A secret field is `{field: {"changed": true}}` |
| `address` | `client_ip(request)` |

Ordered newest first, indexed on `(target_kind, target_id)` and `actor`. Kept forever. No
edit or delete anywhere.

`target_name` and the old and new values are source data captured when the write happened,
not display text. The wording comes from a describer on read (convention 14).

## Recording

`apps/console/audit.py`:

- `record(request, action, target=None, *, changes=None)` writes one entry. `target` is a
  model instance or a `(kind, id, name)` triple.
- `diff(before: dict, after: dict, *, secret=frozenset())` returns only the fields that
  changed, with secrets reduced to `{"changed": True}`.

Actions:

- `route`, `channel`, `provider`, `key`: `.add`, `.edit`, `.delete`
- `user`: `.add`, `.edit`, `.delete`, `.set_password`, `.promote`
- `node.policy`, `node.allow_ports`, `node.reevaluate`, `alert.reevaluate`
- `incident.acknowledge`, `incident.resolve`, `incident.close`, `incident.rerun`
- `run.retry`, `run.abandon`, `inbox.drain`, `inbox.drain_oldest`, `inbox.reclaim`
- `session.sign_in`, `session.sign_out`, `session.sign_in_failed`

Rules:

- Record after the write succeeds, in its transaction. Refused writes (guards, 409s, invalid
  forms) and writes that changed nothing (an edit with no field changed, an action the
  service refused) record nothing.
- Secrets: user passwords, API key material, and any provider or channel field the forms
  treat as write-only.
- Sign-in, sign-out and failed sign-in come from Django's `user_logged_in`,
  `user_logged_out` and `user_login_failed` signals, connected in `ConsoleConfig.ready`.
  A failed attempt records the username typed as `target_name`, no actor.

## Reading

`/console/activity/`, a System tab after Users.

- Newest first, paginated like the other lists.
- Filters: actor and kind.
- Each row: time, actor, one describer sentence ("Edited route catch-all"), and the changed
  fields as old and new. The target links to its page while the row still exists, through
  the intents registry.
- `apps/console/describing.py` or the existing describer home words each action. An unknown
  action key renders a fallback sentence, never the raw key.

## Tests

- Each write path records exactly one entry with the right action, target and changes.
- No entry ever contains a secret's value.
- Refused, invalid and rolled-back writes record nothing.
- Sign-in, sign-out and failed sign-in record entries, and a failed one has no actor.
- A deleted user's entries keep `actor_name`.
- The page lists, filters, paginates and links. The guard test covers it.
- 100% branch coverage on changed code.

## Out of scope

- Writes outside the console: the shell, management commands, webhooks.
- Retention and pruning.
- Export.
