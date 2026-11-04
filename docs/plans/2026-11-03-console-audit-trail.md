---
title: "Console Audit Trail"
parent: Plans
---

# Console audit trail implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every console write records who did what to which object, and the console shows it
on an Activity page.

**Architecture:** An `AuditEntry` model in `apps/console`, a `record()` and `diff()` helper in
`apps/console/audit.py` called explicitly by each write path inside its transaction, auth
signals for sign-in and sign-out, and a paginated `/console/activity/` page worded by a
describer.

**Tech Stack:** Django 5.2, pytest-django, htmx, Primer CSS.

Design: `docs/plans/2026-11-03-console-audit-trail-design.md`. Read it first. It lists the
action keys, the rules for what is and isn't recorded, and which fields are secrets.

Rules for every task:

- TDD: write the failing tests, watch them fail, implement, watch them pass.
- Commit with `git commit` so pre-commit runs. Messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Docs-only commits may use
  `--no-verify`.
- Stage files by name. Never stage `.agent-tree/` or
  `docs/plans/2026-09-14-refire-staleness-open-question.md`. Never push.
- No em or en dashes in code, docs or messages. Never use gate, seam, wire, leverage, crucial,
  navigate or align. Minimal comments.
- Match the console's existing patterns: read models in `apps/console/queries/`, views on
  `ConsoleView`, Description/Action/Fact from `config/describing.py`, intents in
  `apps/console/intents.py`. Look at the Users page (`queries/users.py`, `views/users.py`,
  `templates/console/users.html`) as the closest model for the Activity page.
- 100% branch coverage on changed code.

---

### Task 1: Model and helper

**Files:**
- Create: `apps/console/models.py`, its migration (`uv run python manage.py makemigrations console`)
- Create: `apps/console/audit.py`
- Test: `apps/console/_tests/test_audit.py`

**Model** as in the design. `action`, `target_kind`, `actor_name`, `target_name` are
`CharField`s (`target_name` up to 255, truncated on write). `target_id` is a nullable
`BigIntegerField`. `changes` is `JSONField(default=dict)`. `address` is
`CharField(max_length=64, blank=True)`. `Meta.ordering = ["-at", "-pk"]`, indexes on
`(target_kind, target_id)` and `actor`.

**`record(request, action, target=None, *, changes=None)`**:
- `actor` is `request.user` when authenticated, else None. `actor_name` is its username.
- `target` is a model instance (kind is `model._meta.model_name`, id is `pk`, name is
  `str(instance)`, or a `(kind, id, name)` tuple.
- `address` is `client_ip(request)`.
- Returns the entry.

**`diff(before, after, *, secret=frozenset())`**: returns `{field: {"old": b, "new": a}}` for
each key whose value differs, and `{field: {"changed": True}}` for a differing secret key.
Keys only in one dict count as changed. Values must be JSON-serializable: convert dates,
decimals and model instances to strings in the caller's snapshot, not here.

Add `snapshot(instance, fields)` that returns `{f: value}` with values made
JSON-safe (str for anything `json.dumps` refuses).

**Tests:** `diff` with equal dicts gives `{}`, with changed, added and removed keys, and with a
secret key never contains the value. `record` with an instance, with a triple, with no target,
signed in and signed out, and behind a trusted proxy (`TRUSTED_PROXIES`) records the client IP.
An entry survives its actor's deletion with `actor` None and `actor_name` kept.

Commit: `feat(console): an audit entry model and its recorder`

---

### Task 2: Sign-in and sign-out

**Files:** `apps/console/apps.py` (`ready` connects the receivers), `apps/console/audit.py`
(the receivers), tests in `apps/console/_tests/test_audit_sessions.py`.

- `user_logged_in` records `session.sign_in`, target the user.
- `user_logged_out` records `session.sign_out` when `user` isn't None.
- `user_login_failed` records `session.sign_in_failed` with target
  `("user", None, credentials.get("username", ""))` and no actor. `request` may be None (a
  shell `authenticate()` call). Record nothing then.

Tests go through `client.post(reverse("console:login"), ...)` and `console:logout`. A wrong
password and a non-superuser both record a failed sign-in. The receivers use
`dispatch_uid` so they connect once.

Commit: `feat(console): sign-ins and sign-outs are audited`

---

### Task 3: Config writes

**Files:** the write paths of routes, channels, providers and keys: `views/route.py`,
`route_delete.py`, `channel.py`, `channel_delete.py`, `provider.py`, `provider_delete.py`,
`key.py`, `key_delete.py` and the query functions they call. Tests next to each view's tests.

For each:
1. Read the view and its query function. Find the one place the write succeeds.
2. Take `snapshot(instance, fields)` of the edited fields before and after the save. Use the
   form's own field list. For add, `before` is `{}`. For delete, `after` is `{}` and the
   target name is captured before the delete.
3. Call `record(request, "<kind>.add|edit|delete", instance, changes=diff(...))` inside the
   same `transaction.atomic()` as the write. If the write isn't in one yet, wrap the write
   and the record together.
4. An edit whose diff is empty records nothing.

Secrets: API key material (the raw key and its hash), and every provider or channel field
the form treats as write-only (look for the "a blank field keeps the stored value" handling
in `apps/intelligence/forms.py` and the channel form). Pass them in `secret=`.

Tests, per kind: add, edit and delete each record one entry with the right action, target
and changes. A no-op edit, an invalid form and a refused delete record nothing. Secret
values never appear in `AuditEntry.changes` (assert on the JSON text). One test per kind
that a failure after the write rolls both back: patch `record` to raise and assert the row
didn't change.

Commit: `feat(console): config writes are audited`

---

### Task 4: Users

**Files:** `views/user.py`, `user_delete.py`, `user_password.py`, `user_promote.py`,
`queries/users.py`. Tests next to each.

- `user.add` and `user.edit`: fields username, email, first_name, last_name, is_active.
  The password on add is a secret (`{"password": {"changed": true}}`).
- `user.set_password`: no changes besides `{"password": {"changed": true}}`.
- `user.promote`: `{"is_superuser": {"old": false, "new": true}}`.
- `user.delete`: target name captured before the delete.
- Record inside the existing `select_for_update` transactions in `queries/users.py`. A
  refusal (last superuser, self, gone, busy database) records nothing.

Commit: `feat(console): user changes are audited`

---

### Task 5: Operator actions

**Files:** `views/incident_action.py`, `incident_rerun.py`, `alert_reeval.py`,
`node_reeval.py`, `node_policy.py`, `node_allow_ports.py`, `run.py`, `inbox.py`, and what they
call. Tests next to each.

- `incident.acknowledge|resolve|close`: record only when the outcome is ok. A resolve with a
  summary records `{"summary": {"old": "", "new": text}}`.
- `incident.rerun`, `alert.reevaluate`, `node.reevaluate`: record when the action ran. Put
  its counts in `changes` only if the action already returns them, as `{"result": {...}}`.
- `node.policy`: diff of the node's policy before and after, per checker key.
- `node.allow_ports`: the ports added.
- `run.retry`, `run.abandon`: only when the write won the race.
- `inbox.drain`, `inbox.drain_oldest`, `inbox.reclaim`: target the run when there is one,
  else `("inbox", None, "inbox")`.

Where the write lives in another app's service (`IncidentManager.transition`, `inbox.requeue`)
don't import console code there. Record in the console view after the service returns
success, inside a `transaction.atomic()` that wraps both the call and the record.

Tests: each action records one entry when it succeeds and none when refused.

Commit: `feat(console): operator actions are audited`

---

### Task 6: The Activity page

**Files:**
- Create: `apps/console/queries/activity.py`, `apps/console/views/activity.py`,
  `templates/console/activity.html`
- Modify: `apps/console/urls.py`, the System sub-nav (find where Users is listed),
  `apps/console/intents.py` (`view_activity`), the describer
- Test: `apps/console/_tests/queries/test_activity.py`, `_tests/views/test_activity.py`,
  the guard test's world (`build_world()`) gets a few entries.

- Wording: a table from action key to a sentence template, in the module the console uses
  for describers (follow `config/describing.py` conventions), e.g. `route.edit` gives
  "Edited route {target}". An unknown key gives "Changed {kind} {target}". Failed sign-in
  gives "Failed sign-in as {target}".
- Changes render as a small list: field label, old, new. A secret renders "changed". Field
  names render as words (`first_name` as "first name"), never as a `snake_case` token.
- The target links through an intent when its row still exists (`view_route`, `view_user`,
  and so on). A deleted target renders as text.
- Filters: `actor` (a user pk) and `kind` (`target_kind`), as query parameters, with a
  select for each. Paginate the same way the other console lists do.
- `/console/activity/` is a `ConsoleView`, so `test_urls.py` covers it.

Tests: the list orders newest first, filters by actor and by kind, paginates, links an
existing target, doesn't link a deleted one, shows a deleted actor's name, never shows a
secret value, and the guard test passes.

Commit: `feat(console): an Activity page lists the audit trail`

---

### Task 7: Docs

- `apps/console/AGENTS.md`: the audit rule. Every new console write calls `record()` in its
  transaction. The action keys live in one registry with their wording.
- `AGENTS.md`: the `console` row in the core apps table names `AuditEntry` as its model.
- `docs/Security.md`: an "Audit trail" section. What's recorded, that secrets are recorded
  as changed only, that writes outside the console aren't recorded, and that entries are
  kept forever.
- `docs/Architecture.md`: add `/console/activity/` to the console path table.

Voice check:

```bash
git diff origin/main -- '*.md' ':!docs/plans' | grep -nP "^\+.*(\x{2014}|\x{2013}|\bgate\b|\bseam\b|\bwire\b|leverage|crucial|navigate|\balign)"
```

Commit: `docs: the console audit trail`

---

### Task 8: Verify

1. `uv run pytest`. All pass.
2. `uv run coverage run -m pytest && uv run coverage report --include="apps/console/*" --skip-covered --show-missing`.
   Every changed line covered.
3. `uv run ruff check . && uv run black . --check && uv run mypy .`. Clean.
4. `uv run python manage.py makemigrations --check --dry-run`. No changes.
5. Run the server, make a few writes, screenshot `/console/activity/`.
