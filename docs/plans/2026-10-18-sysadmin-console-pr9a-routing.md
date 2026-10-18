---
title: "Sysadmin Console PR 9a: Routing"
parent: Plans
---

{% raw %}

# Sysadmin console PR 9a: routes, channels and the map

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Routes and notification channels get console pages with typed editors, stale-save
refusal and delete confirmation. The network map becomes the console route list.
`PipelineDefinitionAdmin` and `NotificationChannelAdmin` drop to stock and read-only, and the
admin map goes.

**Architecture:** Each app owns its rules and its one write. `apps/orchestration` owns the
match vocabulary (`routing.py`), the route form (`forms.py`) and `save_route`, a conditional
`UPDATE` on `version`. `apps/notify` owns `ConfigField` declarations on each driver, the channel
form (`forms.py`) and `save_channel`, a conditional `UPDATE` on a row digest. `config/netmap.py`
stops building admin URLs and returns words and `Action`s. `apps/console` owns the reads
(`queries/routes.py`, `queries/channels.py`) and the pages (`views/routes.py`,
`views/route.py`, `views/channels.py`, `views/channel.py`). Design:
`docs/plans/2026-10-18-sysadmin-console-pr9a-routing-design.md`.

**Tech stack:** Django 5.2 views, forms and formsets, templates, pytest-django.

**Rules for every task:** absolute imports. No comments that restate code. A line length of 100.
No wording on a page carries a UUID, a `snake_case` token or a `{` outside Details. Stored enum
values (`incoming_webhook`, `checker_generated`) render through their `TextChoices` label. A
driver key renders through the driver's `label`. Run `uv run black . && uv run ruff check .
--fix` before each commit. Pre-commit runs black, ruff, the full pytest suite and mypy, so a
commit takes several minutes. End each commit message with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

A dataclass this plan lists as "fields: a, b, c" is `@dataclass(frozen=True)` with exactly those
fields, typed as the code that fills it implies.

## Where this plan corrects the design

1. **The map is the route list.** A separate list and map would draw the same lanes in the same
   order twice. `/console/routes/` renders one card per lane from `get_map_context()`: state,
   conditions in words, stages, delivery and the edit link. There is no `/console/routes/map/`.
2. **The admin map goes now, not in PR 11.** Its data shape changes in Task 7 (words and
   `Action`s instead of admin URLs), and every link on it would open a read-only admin page.
   `MonitoringAdminSite.map_view`, its URL, `templates/admin/map.html`, `SECTION_LINKS` and
   `_section_links` go in Task 12. The admin dashboard's "Network map" link points at
   `console:routes`.
3. **Tags stay out of the editor.** `tags` is a dict whose one live key is `seed_shape`, a
   machine flag (`apps/orchestration/seeding.py`). Editing it by hand would put JSON back on the
   page. The editor keeps stored tags as they are, and Details shows them.
4. **`config/netmap.py` can't import `apps.console`.** Upstream returns intents, never URLs, so
   the map's links become `Action`s the console renders through `intents.py`.
5. **There is no room in the nav for two more items.** Routes and Channels sit under one
   **System** item with the `gear` octicon, and a sub-nav partial lists them. 9b adds Providers
   and API keys to the same sub-nav. The sprite gains `gear` (Task 1).
6. **A route the rows can't express keeps its match.** `matches()` fails closed on an unknown op,
   a non-dict condition or a non-list membership value. Such a match can't be drawn as rows. The
   editor says "These conditions use a shape the editor can't show. Saving keeps them.", shows
   them in Details, and offers a "Replace them" checkbox that opens empty rows.
7. **Enumerated match values are checked.** A typo in `severity is critcal` never matches and
   nothing says so. The condition form validates severity, status and origin values against
   `AlertSeverity`, `AlertStatus` and `PipelineOrigin`.
8. **A view-only reader opens the same page.** The route and channel pages render their form
   with every field disabled and no save button for a user without `change_*`. A user with
   neither `view_*` nor `change_*` gets 403.
9. **Channel templates hold `{`.** The template textareas sit in a `<details data-details>`
   group titled "Message templates", so the guard test skips them like any other Details.
10. **A driver this hub doesn't have.** `driver` is a free `CharField`, and the console fixture
    already holds `carrier_pigeon`. The driver select shows such a value as "A driver this hub
    doesn't have", keeps it selected, renders no typed fields, and keeps the whole config as
    unknown keys.
11. **`version` was "bumped manually".** Its help text changes to "Bumped by every console save."
    with a migration that only alters the help text.
12. **Changing driver needs a round trip.** Without JS, the driver select has a "Show this
    driver's settings" submit button (`name="switch_driver"`) that re-renders the form for the new
    driver and saves nothing. A new channel starts at `/console/channels/new/?driver=slack`, and
    without `driver` the page lists the drivers as links.

---

### Task 1: The gear octicon and the System nav

**Files:**
- Modify: `static/console/vendor/octicons.svg`, `static/console/vendor/VENDOR.md`
- Modify: `templates/console/base.html`
- Create: `templates/console/partials/_system_nav.html`
- Test: `apps/console/_tests/test_vendor.py` (hash only), `apps/console/_tests/test_templates.py`

**Step 1:** Fetch the icon at the pinned version, as VENDOR.md says:

```bash
curl -sSfL https://unpkg.com/@primer/octicons@19.15.1/build/svg/gear-16.svg
```

Add its `<path>` elements unchanged as `<symbol id="octicon-gear" viewBox="0 0 16 16">`, keeping
the sprite's alphabetical order. Recompute the hash with `shasum -a 256` and update the
`octicons.svg` row.

**Step 2:** Write the failing template test:

```python
def test_the_nav_has_a_system_item(client, superuser):
    client.force_login(superuser)
    body = client.get("/console/").content.decode()
    assert 'href="/console/routes/"' in body
    assert "#octicon-gear" in body
```

Run: `uv run pytest apps/console/_tests/test_templates.py -k system -v`. Expected: FAIL.

**Step 3:** In `base.html` add after Nodes:

```html
<a class="c-unav__item" href="{% url 'console:routes' %}"{% if nav == "system" %} aria-current="page"{% endif %}>{% include "console/partials/_octicon.html" with name="gear" %}<span>System</span></a>
```

`_system_nav.html` takes `section` and renders a `c-subnav` with Routes (`console:routes`) and
Channels (`console:channels`), marking the current one with `aria-current="page"`. If
`console.css` has no `c-subnav`, add one built from the `c-unav` rules at a smaller size. The
`console:routes` URL comes in Task 7, so this test passes after Task 7. Commit Task 1 with Task 7.

---

### Task 2: The match vocabulary

**Files:**
- Create: `apps/orchestration/routing.py`
- Test: `apps/orchestration/_tests/test_routing.py`

`routing.py` is the one place that knows the match fields, ops and how they read.

```python
FIELDS: dict[str, str] = {
    "source": "Source",
    "severity": "Severity",
    "status": "Status",
    "instance": "Host",
    "origin": "Started by",
    "label": "Label",
}
OPS: dict[str, str] = {"is": "is", "is-not": "is not", "in": "is one of", "not-in": "is not one of"}
MEMBERSHIP = ("in", "not-in")
CHOICES: dict[str, type[models.TextChoices]] = {
    "severity": AlertSeverity,
    "status": AlertStatus,
    "origin": PipelineOrigin,
}
```

Functions:

- `split_field(field: str) -> tuple[str, str]`: `"label:team"` → `("label", "team")`, `"source"`
  → `("source", "")`.
- `value_words(kind: str, value: str) -> str`: the `TextChoices` label when `kind` is in `CHOICES`
  and the value is a member, else the value as sent.
- `describe_condition(cond) -> str | None`: `None` when the condition can't be expressed (not a
  dict, unknown field, unknown op, membership without a list of strings, `is` without a string).
  Otherwise "Severity is Critical", "Label team is one of web, db", "Started by is Incoming
  webhook".
- `describe_match(match) -> str`: "Matches everything" for `[]` or `None`. Otherwise the
  conditions joined with " and ". Any inexpressible condition makes it "Uses conditions the
  console can't show".
- `representable(match) -> bool`: a list whose every condition `describe_condition` can read.

**Steps:** Write tests for each: every field, every op, a label field, a choices label, a value
not in its choices passes through, each inexpressible shape returns `None`, the empty match, the
mixed match. Run to fail, implement, run to pass, commit
`feat(orchestration): route conditions in words`.

---

### Task 3: Route stale save and delete consequence

**Files:**
- Modify: `apps/orchestration/routing.py`
- Create: `apps/orchestration/migrations/00NN_pipelinedefinition_version_help.py` (generated)
- Modify: `apps/orchestration/models.py` (help text only)
- Test: `apps/orchestration/_tests/test_routing.py`

```python
class StaleRoute(Exception):
    """The route changed between the editor's render and its save."""


def save_route(pk: int, fields: dict, *, expected: int) -> None:
    """Write ``fields`` to route ``pk`` iff it is still at version ``expected``. Bumps it."""
    updated = PipelineDefinition.objects.filter(pk=pk, version=expected).update(
        **fields, version=F("version") + 1, updated_at=timezone.now()
    )
    if not updated:
        raise StaleRoute
```

`QuerySet.update` skips `auto_now`, so `updated_at` is set by hand.

`fallthrough(lane) -> str` answers "what happens to traffic this route matched once it's gone":

- Not active: "This route is off, so deleting it changes no routing."
- Otherwise the first later active lane (by `priority`, `id`) with an empty match:
  "Traffic it matched goes to the next route that matches, at worst <name>, which matches
  everything."
- None: "Traffic only it matched will match no route, and those runs will fail as unrouted."

**Steps:** Tests: a save at the current version writes and bumps; a save at an old version raises
and writes nothing; `updated_at` moves. Each `fallthrough` branch, including an inactive
catch-all being skipped and an earlier catch-all being ignored. Change the `version` help text to
"Bumped by every console save." and run `uv run python manage.py makemigrations orchestration`.
Commit `feat(orchestration): a route save that refuses a stale editor`.

---

### Task 4: The route form

**Files:**
- Create: `apps/orchestration/forms.py`
- Test: `apps/orchestration/_tests/test_forms.py`

`ConditionForm(forms.Form)`:

- `field`: `ChoiceField(choices=FIELDS.items())`
- `label_key`: `CharField(required=False, label="Label name")`
- `op`: `ChoiceField(choices=OPS.items())`
- `value`: `CharField(help_text="For “is one of”, separate values with commas.")`
- `clean()`: a `label` field needs `label_key`. Membership splits on commas, strips, drops
  blanks, and needs at least one value. For a field in `CHOICES`, every value must be a member,
  else "Not a <field label>: <value>. Use one of <labels>." Produces `self.condition`:
  `{"field": "label:<key>" | field, "op": op, "value": str | list[str]}`.
- `initial_for(cond) -> dict`: the reverse, for one representable condition.

`ConditionFormSet = forms.formset_factory(ConditionForm, extra=0, can_delete=True)`, prefix
`cond`.

`RouteForm(forms.ModelForm)` on `PipelineDefinition`, fields `name`, `description`, `is_active`,
`priority`, `channel`:

- `stages`: `MultipleChoiceField(widget=CheckboxSelectMultiple, required=False)` with choices
  `(stage, STAGE_NAMES[stage]) for stage in ROUTABLE_STAGES`. Initial is the stored
  `routable_stages()`, or every stage on a new route. `clean_stages` returns them in
  `ROUTABLE_STAGES` order.
- `channel`: label "Channel", `required=False`, `label_from_instance` appends " (off)" to an
  inactive channel.
- `replace_match`: `BooleanField(required=False, label="Replace them")`, present only when the
  stored match isn't `representable`.
- `__init__(data=None, *, instance=None, disabled=False)` builds `self.conditions`, a
  `ConditionFormSet`, from the stored match. `disabled=True` disables every field in both.
- `add_condition(data) -> QueryDict` (static): a copy of `data` with `cond-TOTAL_FORMS` one
  higher.
- `is_valid()` is the form and the formset together.
- `route_fields() -> dict`: the cleaned model fields, plus `stages`, plus `match`. The match is
  the formset's conditions minus deleted rows, or the stored match unchanged when it isn't
  representable and `replace_match` is off.

**Steps:** Tests: stored match round-trips through `initial_for` and `route_fields`; a label
condition; membership splitting; a bad severity is an error naming the choices; a deleted row is
dropped; no rows is `[]`; an unrepresentable match is kept, and replaced when the box is ticked;
`add_condition` bumps the count; stages come back in canonical order; `disabled` disables
everything; an inactive channel reads "(off)". Commit `feat(orchestration): a route form with
condition rows`.

---

### Task 5: Driver config fields

**Files:**
- Create: `apps/notify/config_fields.py`
- Modify: `apps/notify/drivers/base.py`, `slack.py`, `email.py`, `pagerduty.py`, `generic.py`
- Test: `apps/notify/_tests/test_config_fields.py`

```python
@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    kind: str
    required: bool = False
    secret: bool = False
    default: Any = None
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()


KINDS = ("text", "url", "int", "bool", "choice", "list", "template", "headers")
```

`BaseNotifyDriver.config_fields: tuple[ConfigField, ...] = ()` and a classmethod
`all_config_fields()` that returns the driver's own fields, then `COMMON_FIELDS`:

```python
COMMON_FIELDS = (
    ConfigField("channel", "Default destination", "text",
                help="Where messages go when the alert names none."),
    ConfigField("template", "Message template", "template"),
    ConfigField("html_template", "HTML template", "template"),
)
```

Per driver, in this order:

| Driver | Fields |
|---|---|
| slack | `webhook_url` url required secret, `timeout` int default 30 |
| email | `smtp_host` text required, `smtp_port` int default 587, `use_tls` bool default True, `use_ssl` bool default False, `username` text, `password` text secret, `from_address` text required, `to_addresses` list, `timeout` int default 30 |
| pagerduty | `integration_key` text required secret, `event_action` choice (trigger, acknowledge, resolve) default trigger, `client` text, `client_url` url, `timeout` int default 30 |
| generic | `endpoint` url secret, `method` choice (POST, PUT) default POST, `headers` headers secret, `timeout` int default 30, `payload_template` template, `disabled` bool default False |

`headers` is always secret: `ConfigField.__post_init__` raises `ValueError` for a `headers` field
without `secret=True`, and for a `kind` not in `KINDS`.

**Guard test:** for each driver in `DRIVER_REGISTRY`, read its module source and `base.py` with
`inspect.getsource`, collect every `config.get("x")` and `config["x"]` key by regex, and assert
each is declared in `all_config_fields()` or listed in

```python
ALIASES = {"generic": {"webhook_url"}, "*": {"text_template"}}
```

with a one-line reason each in the test's docstring: generic accepts `webhook_url` as an old
spelling of `endpoint`, and the templating service accepts `text_template` for `template`. Both
are kept as unknown keys.

**Steps:** Write the guard and the `__post_init__` tests, fail, declare, pass. Commit
`feat(notify): drivers declare their config fields`.

---

### Task 6: The channel form and save

**Files:**
- Create: `apps/notify/forms.py`, `apps/notify/channels.py`
- Test: `apps/notify/_tests/test_forms.py`, `apps/notify/_tests/test_channels.py`

`channels.py`:

```python
class StaleChannel(Exception):
    """The channel changed between the editor's render and its save."""


def channel_digest(row: dict) -> str:
    """name, driver, is_active, description and config, sorted JSON, SHA-256."""


def save_channel(pk: int, values: dict, *, expected: str) -> None:
    """Write ``values`` iff the stored row still hashes to ``expected``."""
```

`save_channel` reads the five fields with `.values()`, compares the digest, then runs one
`filter(pk=pk, **stored).update(**values, updated_at=timezone.now())` and raises `StaleChannel`
on zero rows, as `save_policy` does.

`lanes_cut_by_delete(channel) -> list[PipelineDefinition]`: the lanes on this channel that list
`notify`, ordered by `priority`, `id`. Deleting sets their channel to `NULL`, so each of them
stops delivering.

`forms.py`, `ChannelForm(forms.Form)`:

- `name`, `description`, `is_active` as on the model. `name` checks uniqueness against other
  rows.
- `driver`: `ChoiceField` of `(key, cls.label)` from `DRIVER_REGISTRY`. A stored driver not in
  the registry is added as `(key, "A driver this hub doesn't have")`.
- One field per `ConfigField` of the selected driver, named `cfg__<key>`:
  - `text`: `CharField`. `url`: `CharField` with `URLValidator(schemes=["http", "https"])`.
    `int`: `IntegerField(min_value=1)`. `bool`: `BooleanField`. `choice`: `ChoiceField`.
    `list`: `CharField(widget=Textarea)`, one value per line. `template`:
    `CharField(widget=Textarea)`. `headers`: `CharField(widget=Textarea)`, one `Name: value` per
    line.
  - Every field is `required=False` at the Django level. `clean()` enforces `required`, so a
    stored secret counts as set.
  - A secret uses `PasswordInput(render_value=False)` or a `Textarea` rendered empty. Its help
    reads "Set. Leave blank to keep it." or "Not set.". It gets a `cfg__<key>__clear` checkbox,
    "Clear", only when a value is stored.
- `__init__(data=None, *, channel=None, driver=None, disabled=False)`. `driver` overrides the
  stored one (the `?driver=` and `switch_driver` paths).
- `config() -> dict`, valid only after `is_valid()`. Start from the stored config. When the driver
  changed, drop every key the old driver declares as secret. For each declared field: a secret
  that's blank and not cleared keeps the stored value, and a cleared one drops it. A blank optional
  non-secret field drops its key, so the driver's default applies. Every other field writes its
  cleaned value. Unknown keys stay.
- `clean()` runs `DRIVER_REGISTRY[driver]().validate_config(config)` for a known driver. False is
  a non-field error: "<Label> can't send with these settings. Check the required fields."
- `unknown_keys() -> list[str]`: stored keys the driver doesn't declare, for Details.
- `groups() -> list[tuple[str, list[BoundField]]]`: "Settings" (non-template fields, each followed
  by its Clear box) and "Message templates".

**Steps:** For each driver, a round-trip test: build from a stored config, submit the initial
values unchanged, and `config()` equals the stored config. Then: secret kept on blank; secret
cleared; a required secret with nothing stored is an error; an optional int left blank drops its
key; list and headers parsing; an unknown key survives; switching slack → email drops
`webhook_url`; the unknown driver keeps everything and renders no typed field; `validate_config`
false is the non-field error; `disabled` disables all; the duplicate name is an error. For
`save_channel`: a fresh save writes; a stale digest raises; a race (row changed after the read)
raises. For `lanes_cut_by_delete`: a lane without `notify` isn't listed. Commit
`feat(notify): a typed channel form that never shows a secret`.

---

### Task 7: The map speaks in words and actions, and the routes page

**Files:**
- Modify: `config/netmap.py`, `config/_tests/test_netmap.py`
- Create: `apps/console/queries/routes.py`, `apps/console/views/routes.py`
- Create: `templates/console/routes.html`, `templates/console/partials/_routes.html`
- Modify: `apps/console/urls.py`, `apps/console/intents.py`
- Test: `apps/console/_tests/queries/test_routes.py`, `apps/console/_tests/views/test_routes.py`

`get_map_context()` card changes:

- `"pk"`: the lane's pk. `"admin_url"` goes.
- `"conditions"`: each from `describe_condition`, or "A condition the console can't show".
- `"state_words"`: `{"ok": "", "inactive": "Off", "never-matches": "Never matches",
  "shadowed": "Never reached: <shadowed_by> catches its traffic first"}`.
- `"stages"`: `[STAGE_NAMES[s] for s in lane.routable_stages()]`, or "Records only" when empty.
- `"delivery"`: `{"state", "channel", "words", "action"}`. `words` is "Delivers to <channel>",
  "Records only, sends nothing", or `"Can't deliver: " + REASONS[gap].headline`. `action` is
  `Action("Open <channel>", "edit_channel", {"pk": ...})` when bound, else
  `Action("Give it an active channel", "edit_route", {"pk": lane.pk})`, else `None`.
- `"edit"`: `Action("Edit route", "edit_route", {"pk": lane.pk})`.
- `"seed_note"`: "Set up without delivery. It delivers once a channel is active." when
  `seed_shaped`, else "".

`netmap.py` no longer imports `config.admin_links` or `reverse`.

`queries/routes.py`: `build_route_list() -> RouteList`, fields: `cards` (the map cards, each with
`edit` and `delivery.action` resolved by the console's `resolve` helper, as other queries do),
`can_add`, `summary`. `summary` is "N routes, evaluated top to bottom. The first that matches
wins."

Intents:

```python
ROUTE_READ = ("orchestration.view_pipelinedefinition", "orchestration.change_pipelinedefinition")
"view_routes": Intent(_page("console:routes"), "get", ROUTE_READ),
"edit_route": Intent(_route, "get", "orchestration.change_pipelinedefinition"),
"add_route": Intent(_page("console:route-new"), "get", "orchestration.add_pipelinedefinition"),
"delete_route": Intent(_record("console:route-delete"), "get",
                       "orchestration.delete_pipelinedefinition"),
```

`_route(params)` is `console:route` with `pk` when given, else `console:routes`, so the
parameterless `REASONS` action keeps working.

`RoutesView(ConsoleView)`: `read_permissions = ROUTE_READ`, `nav = "system"`,
`section = "routes"`. Template: pagehead "Routes", the summary, an "Add a route" button when
`can_add`, then one `c-box` per card in order: name and state chip, conditions as a list,
stages, delivery words with its action, seed note, Edit. A lane in state `inactive` or
`shadowed` is muted. URL: `path("routes/", RoutesView.as_view(), name="routes")`.

**Steps:** Update `test_netmap.py` for the new card keys, and test each state's words and each
delivery branch. View tests: renders in order; 403 for bare staff; a view-only user sees no "Add
a route"; the shadowed lane names its shadower; the parameterless `edit_route` resolves to the
list. Commit Task 1 and Task 7 together: `feat(console): the routes page, drawn from the map`.

---

### Task 8: The route editor

**Files:**
- Create: `apps/console/views/route.py`, `templates/console/route.html`,
  `templates/console/partials/_route.html`, `templates/console/partials/_condition_row.html`
- Modify: `apps/console/queries/routes.py`, `apps/console/urls.py`
- Test: `apps/console/_tests/views/test_route.py`

URLs: `routes/new/` (`route-new`), `routes/<int:pk>/` (`route`).

`build_route_editor(pk: int | None, data=None, *, can_change: bool) -> RouteEditor | None`,
fields: `pk`, `name`, `form`, `version`, `url`, `back_url`, `represented`, `stored_conditions`,
`facts`. `facts` holds created by, created, updated, version and tags (as
`Fact("Tags", json.dumps(tags, sort_keys=True))` when non-empty).

`RouteView(ConsoleView)`:

- `read_permissions = ROUTE_READ`. `can_change` is `change_pipelinedefinition` for an existing
  route and `add_pipelinedefinition` for a new one. GET of `route-new` without add permission is
  403.
- GET renders the editor. The form is disabled when `not can_change`.
- POST without `can_change` is 403.
- POST with `add_condition` re-renders with `RouteForm.add_condition(request.POST)` and saves
  nothing.
- POST invalid re-renders with errors at 200.
- POST valid on a new route creates it with `created_by=request.user.get_username()` and
  `version=1`, then redirects 303 to the routes page with "Route added."
- POST valid on an existing route calls `save_route(pk, form.route_fields(),
  expected=int(request.POST["version"]))`. `StaleRoute` or `OperationalError` re-renders a fresh
  editor at 409 with "This route changed since you opened it. Here it is now." Success redirects
  303 to the routes page with "Route saved."
- An unknown pk is the console 404.

Template: pagehead "Route <name>" or "New route". Fields as `_field.html`. Stages as checkboxes.
"Conditions" box: "All of these must hold. No conditions matches everything." Then the formset
management form and one `_condition_row.html` per form: field, label key, op, value and the
delete box labelled "Remove". "Add condition" is `<button name="add_condition" value="1">`. When
not `represented`: the warning, the stored conditions in a `<details data-details>`, and the
`replace_match` box. Hidden `version`. Save and Cancel. A "Delete route" link when the user may
delete. Details: the facts.

**Steps:** Tests for each POST branch above, including a stale version at 409 with the fresh
values, add-condition saving nothing, a bad severity re-rendering with the error, a new route's
`created_by`, the disabled form for a view-only user, 403 on POST without change, 404 on a missing
pk. Commit `feat(console): edit a route, refusing a stale save`.

---

### Task 9: Delete a route

**Files:**
- Create: `apps/console/views/route_delete.py`, `templates/console/route_delete.html`
- Modify: `apps/console/urls.py`
- Test: `apps/console/_tests/views/test_route_delete.py`

URL: `routes/<int:pk>/delete/` (`route-delete`). Needs `delete_pipelinedefinition`, else 403.
GET renders "Delete route <name>?", `fallthrough(lane)`, and a POST form with "Delete route". POST
deletes and redirects 303 to the routes page with "Route <name> deleted." A missing pk is 404.
The form carries `data-disable-on-submit`, which stops a double submit, as the policy editor does.

**Steps:** Tests: the consequence sentence on GET, deletion on POST, 403 for a user with only
change, 404. Commit `feat(console): delete a route, saying where its traffic goes`.

---

### Task 10: The channel list and editor

**Files:**
- Create: `apps/console/queries/channels.py`, `apps/console/views/channels.py`,
  `apps/console/views/channel.py`
- Create: `templates/console/channels.html`, `templates/console/partials/_channels.html`,
  `templates/console/channel.html`, `templates/console/partials/_channel.html`,
  `templates/console/channel_new.html`
- Modify: `apps/console/urls.py`, `apps/console/intents.py`
- Test: `apps/console/_tests/queries/test_channels.py`, `apps/console/_tests/views/test_channels.py`,
  `apps/console/_tests/views/test_channel.py`

Intents:

```python
CHANNEL_READ = ("notify.view_notificationchannel", "notify.change_notificationchannel")
"view_channels": Intent(_page("console:channels", "q", "name"), "get", CHANNEL_READ),
"edit_channel": Intent(_channel, "get", "notify.change_notificationchannel"),
"add_channel": Intent(_page("console:channel-new"), "get", "notify.add_notificationchannel"),
"delete_channel": Intent(_record("console:channel-delete"), "get",
                         "notify.delete_notificationchannel"),
```

`_channel(params)`: `console:channel` with `pk`, else the list filtered by `name` as `q`, else the
list. The readiness `edit_channel` actions pass `name`, so they keep working.

`build_channel_list(q: str) -> ChannelList`, fields: `rows`, `q`, `can_add`. Each row
(`ChannelRow`): `pk`, `name`, `driver_label`, `active`, `lanes` (tuple of `LaneLine`: `name`,
`gap_words`, `action`), `edit`. `gap_words` is `REASONS[gap].headline` or "". The rows are
ordered by name, with `q` matching name, case-insensitive. Lanes come from one query with
`select_related("channel")`.

URLs: `channels/` (`channels`), `channels/new/` (`channel-new`), `channels/<int:pk>/`
(`channel`).

`ChannelView` follows `RouteView`:

- GET `channel-new` without `?driver=` renders `channel_new.html`: a link per registry driver,
  `?driver=<key>`, labelled with its `label`.
- POST with `switch_driver` re-renders `ChannelForm(request.POST, driver=request.POST["driver"])`
  with its errors cleared and saves nothing.
- POST valid on new: `NotificationChannel.objects.create(...)`, redirect 303 to the channel page
  with "Channel added."
- POST valid on existing: `save_channel(pk, values, expected=request.POST["digest"])`, where
  `values` holds name, driver, is_active, description and `form.config()`. `StaleChannel` or
  `OperationalError` re-renders at 409 with "This channel changed since you opened it. Here it is
  now." Success redirects 303 to the channel page with "Channel saved."
- The page lists "Routed here by": each lane with its gap words, linking `edit_route`.
- Details: driver key, unknown config keys by name, created, updated.
- A "Delete channel" link when the user may delete.

**Steps:** Tests for each branch, including that no secret value appears anywhere in the response
body (assert the stored `webhook_url` string is absent), the 409, the driver switch saving
nothing, the driver picker, the unknown-driver channel rendering, the view-only disabled form,
403s and 404. Commit `feat(console): channels, with typed settings per driver`.

---

### Task 11: Delete a channel

**Files:**
- Create: `apps/console/views/channel_delete.py`, `templates/console/channel_delete.html`
- Modify: `apps/console/urls.py`
- Test: `apps/console/_tests/views/test_channel_delete.py`

As Task 9. The consequence is `lanes_cut_by_delete`: "These routes will stop delivering:" and the
lane names, or "No route delivers through it." Commit
`feat(console): delete a channel, naming the routes it cuts`.

---

### Task 12: Point everything at the console and cut the admins

**Files:**
- Modify: `config/dashboard.py` (channel and lane `url`s → `console:channels`, `console:routes`)
- Modify: `templates/admin/dashboard.html` (the map link → `console:routes`)
- Modify: `config/admin.py` (drop `map_view`, its URL, `SECTION_LINKS`, `_section_links` and the
  `get_map_context` import)
- Delete: `templates/admin/map.html`
- Modify: `apps/orchestration/admin.py`: `PipelineDefinitionAdmin` becomes stock read-only
  (`list_display = ["name", "priority", "is_active", "updated_at"]`, the three
  `has_*_permission` methods returning `False`, docstring "Read-only. Operators edit routes in
  the console."). `channel_name`, `formfield_for_dbfield`, `save_model`, `get_queryset` and the
  fieldsets go.
- Modify: `apps/notify/admin.py`: `NotificationChannelAdmin` the same, with
  `list_display = ["name", "driver", "is_active", "updated_at"]`. Lane counting and
  `lanes_display` go.
- Modify: `apps/orchestration/_tests/test_admin.py`, `apps/notify/_tests/test_admin.py`,
  `config/_tests/test_dashboard_render.py`, `config/_tests/test_dashboard_readiness.py`,
  `config/_tests/test_admin_site.py`: delete tests of removed behaviour, add one test per admin
  that add, change and delete are refused.

Check that `django_json_widget` still has a user (`apps/intelligence/admin.py` does until 9b).

**Steps:** Change, run `uv run pytest config apps/orchestration apps/notify -q`, fix fallout,
commit `feat!: the route and channel admins are stock and read-only`.

---

### Task 13: The guard test covers routing

**Files:**
- Modify: `apps/console/_tests/fixtures.py`, `apps/console/_tests/test_guard.py`

Fixture additions in `_readiness_faults`: a Slack channel "ops-slack" with a stored
`webhook_url`, a lane "web-critical" with `severity is critical` and
`origin is incoming_webhook`, a shadowed lane behind it, and an inactive lane. `World` gains
`route_pks` and `channel_pks`.

Guard URLs: `/console/routes/`, `/console/routes/new/`, every route page, every route delete
page, `/console/channels/`, `/console/channels/new/`, `/console/channels/new/?driver=<key>` for
each registry driver, every channel page and every channel delete page. Shown strings to add:
"Severity is Critical", "Started by is Incoming webhook", "Never reached", "Can't deliver",
"A driver this hub doesn't have", "Set. Leave blank to keep it.", "Message templates",
"These routes will stop delivering". Also assert the stored `webhook_url` appears on no page.

Commit `test(console): the guard covers routes and channels`.

---

### Task 14: Docs

**Files:**
- Modify: `AGENTS.md` (convention 3: routes and channels are read and edited in the console, and
  the admin map is gone), `apps/console/AGENTS.md` (the System section, the pages, the intents),
  `apps/notify/AGENTS.md` (`config_fields`, the secret rule, the guard test),
  `apps/orchestration/AGENTS.md` (`routing.py`, `save_route`, `version`)
- Modify: `docs/Architecture.md:288` and `docs/Templates.md:127` to name the console pages

Commit `docs: routes and channels live in the console`.

---

## Verification

```bash
uv run black . --check && uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run python manage.py check
uv run python manage.py runserver   # /console/routes/, /console/channels/, edit one of each
```

{% endraw %}
