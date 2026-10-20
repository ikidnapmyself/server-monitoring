---
title: "Sysadmin Console PR 9b: Providers and API Keys"
parent: Plans
---

{% raw %}

# Sysadmin console PR 9b: providers and API keys

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** AI providers and API keys get console pages, with typed provider settings,
write-only secrets, a one-time token reveal, stale-save refusal and delete confirmation.
`IntelligenceProviderAdmin` and `APIKeyAdmin` drop to stock and read-only.

**Architecture:** The typed-settings machinery from 9a moves out of `apps/notify` into
`config/`, a shared utility that notify and intelligence both use:
- `ConfigField` and the field kinds go to `config/config_fields.py`.
- The form core goes to `config/config_form.py`.
- The digest and conditional save go to `config/stale.py`.

Each app owns its own rules and its one write. `apps/intelligence` owns the provider
declarations, `ProviderForm` and `save_provider`. `config/` owns `APIKeyForm` and
`save_key`, since `APIKey` lives there. `apps/console` owns the reads and the pages, which
mirror the 9a channel pages file for file. Design:
`docs/plans/2026-10-19-sysadmin-console-pr9b-providers-keys-design.md`.

**Tech stack:** Django 5.2 views, forms and templates, and pytest-django.

**Rules for every task:**
- Use absolute imports. No comments that restate code. Line length is 100.
- No wording on a page carries a UUID, a `snake_case` token or a `{` outside Details.
- A provider key renders through its `PROVIDER_CHOICES` label.
- Run `uv run black . && uv run ruff check . --fix` before each commit.
- Pre-commit runs black, ruff, the full pytest suite and mypy, so a commit takes several
  minutes.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Voice: no em or en dashes. None of the words gate, seam, wire, leverage, crucial,
  navigate or align.

The pages copy the 9a channel pages after the restyle in `9b6b9d7`:
- `apps/console/views/channel.py`, `channel_delete.py` and `channels.py`
- `apps/console/queries/channels.py`
- `templates/console/channel*.html` and `partials/_channel*.html`

Read those before Tasks 6 to 9. Keep their behaviour:
- a disabled form for readers
- a 403 per missing permission and the console 404 page
- a 409 that shows the row as it is now
- `transaction.atomic`
- `IntegrityError` blamed on the name only when another row has it
- a 303 with a message after a save

## Where this plan corrects the design

1. `local` isn't in `IntelligenceProvider.PROVIDER_CHOICES`. It's the fallback, never a
   row, so it gets no form and no declarations. The picker lists `PROVIDER_CHOICES`.
2. Labels come from `PROVIDER_CHOICES` through `get_provider_display()`. Providers gain no
   `label`.
3. `save_provider` is a conditional `UPDATE`, so it skips `IntelligenceProvider.save()`,
   which is what turns the other providers off. `save_provider` turns them off itself, in
   the same transaction. A create goes through `.save()` and keeps the model's behaviour.
4. `validate_safe_url` resolves DNS. Form tests patch `socket.getaddrinfo` or use a host in
   `SSRF_ALLOWED_HOSTS`, as `apps/intelligence/_tests` already does for the providers.
5. Re-posting the create response would make a second key. The response sends
   `Cache-Control: no-store`, and the form carries `data-disable-on-submit`. Nothing else
   guards against it.

---

### Task 1: Typed settings move to `config/`

**Files:**
- Create: `config/config_fields.py`, `config/config_form.py`, `config/stale.py`
- Modify: `apps/notify/config_fields.py` (it keeps only `COMMON_FIELDS` and
  `timeout_field`, importing `ConfigField` from config), `apps/notify/forms.py`,
  `apps/notify/channels.py`, and every `apps/notify` import of the moved names
- Test: `config/_tests/test_config_fields.py`, `config/_tests/test_config_form.py`,
  `config/_tests/test_stale.py`

`config/config_fields.py`: `KINDS` and `ConfigField`, as now, plus a `"float"` kind, which
the form renders as a `FloatField` with `min_value=0`.

`config/config_form.py`: `TypedConfigForm(forms.Form)` holds everything in `ChannelForm`
that isn't about channels:
- `PREFIX`, `field_name`, `clear_name`, `SET_HELP`, `UNSET_HELP`, `ALIAS_HELP`,
  `HEADER_ERROR`
- `SecretTextarea`, `TemplateField`, `ListField`, `HeadersField`, `_display`,
  `_typed_field`
- `_add_field`, `_stored_aliases`, `_untouched`, `_build_config` and `unknown_keys`

Its `__init__(data, *, stored: dict, specs, aliases: dict)` sets `_base`, `_specs`,
`_aliases` and `_display` and adds the typed fields. The subclass adds its own model fields
first.

`ChannelForm` subclasses it and keeps:
- `name`, `driver`, `is_active` and `description`
- the switch-drops-credentials rule and `clean_name`
- the driver mismatch error
- `validate_config`, `channel_fields` and `groups`

`config/stale.py`:

```python
class Stale(Exception):
    """The row changed between the editor's render and its save."""


def row_digest(salt: str, row: dict, fields: tuple[str, ...]) -> str: ...
def save_if_unchanged(model, pk: int, fields: tuple[str, ...], values: dict, *,
                      salt: str, expected: str) -> None: ...
```

`row_digest` is today's `channel_digest` body: sorted JSON, then `salted_hmac(...,
algorithm="sha256")`. `save_if_unchanged` is today's `save_channel` body. It raises `Stale`
and sets `updated_at`. `StaleChannel` becomes `class StaleChannel(Stale)`, and
`channel_digest` and `save_channel` become one-line wrappers. The view code and its tests
don't change.

**Steps:**
1. Move the tests of the moved code to `config/_tests` first. Run them and see them fail on
   import.
2. Move the code.
3. Run `uv run pytest apps/notify apps/console config -q`. Every existing notify and console
   test passes without changing a test expectation. Only imports change.
4. Add a test for the float kind.
5. Commit `refactor: typed settings and stale saves move to config`.

---

### Task 2: Providers declare their settings

**Files:**
- Modify: `apps/intelligence/providers/base.py` (`BaseProvider.config_fields: tuple = ()`,
  `unused_config: tuple[str, ...] = ()`, and `all_config_fields()` returning
  `config_fields`)
- Modify: `apps/intelligence/providers/ai_base.py`, `copilot.py`, `grok.py` and
  `ollama.py`
- Test: `apps/intelligence/_tests/test_config_fields.py`

`ai_base.py` adds `AI_FIELDS`:

```python
AI_FIELDS = (
    ConfigField("api_key", "API key", "text", secret=True),
    ConfigField("model", "Model", "text", help="Leave blank for the provider's default."),
    ConfigField("max_tokens", "Most tokens per answer", "int"),
    ConfigField("timeout_s", "Timeout (seconds)", "int"),
)
```

- `BaseAIProvider.config_fields = AI_FIELDS`.
- Copilot and Grok add `ConfigField("base_url", "API address", "url")`, with that class's
  current default as `default`.
- Ollama's `config_fields` is `AI_FIELDS[1:]` plus `ConfigField("host", "Ollama address",
  "url", default="http://localhost:11434")`, and it sets `unused_config = ("api_key",)`.

The guard test runs over every key in `PROVIDER_CHOICES`. It collects the keyword
parameters of every `__init__` in the class's MRO, excluding `self` and `kwargs`. It fails
when:
- a parameter is neither declared nor in `unused_config`
- a declared key isn't a parameter
- an `api_key`-like field isn't secret, matching on `key` or `token` in the name

**Steps:** write the guard test first and watch it fail, then add the declarations, then
commit `feat(intelligence): providers declare their settings`.

---

### Task 3: The provider form and save

**Files:**
- Create: `apps/intelligence/forms.py`, `apps/intelligence/saving.py`
- Test: `apps/intelligence/_tests/test_forms.py`, `apps/intelligence/_tests/test_saving.py`

`ProviderForm(TypedConfigForm)` takes `(data=None, *, provider: IntelligenceProvider |
None = None, kind: str | None = None, disabled: bool = False)`. It mirrors `ChannelForm`,
with `provider` in place of `driver`:
- The choices are `PROVIDER_CHOICES`. A stored kind that isn't a choice gets "A provider
  this hub doesn't have".
- Switching kind drops the old kind's secrets.
- "Show this provider's settings before saving." when the posted kind differs.
- `NAME_TAKEN` reads "Another provider already has this name."

In `clean()`, every non-blank `url` field goes through `validate_safe_url(value,
allowed_hosts=settings.SSRF_ALLOWED_HOSTS)`. On `URLNotAllowedError` it adds a field error:
"This hub won't call that address. Its host must be public, or allowed in the hub's
settings."

`provider_fields()` returns name, provider, is_active, description and config.
`groups()` returns `[("Settings", ...)]`, with Clear boxes after their secret.

`apps/intelligence/saving.py`:
- `FIELDS = ("name", "provider", "is_active", "description", "config")`
- `StaleProvider(Stale)`
- `provider_digest(row)`, salt `"intelligence.provider"`
- `save_provider(pk, values, *, expected)`: inside `transaction.atomic()` it first turns
  every other active row off when `values["is_active"]`, then calls `save_if_unchanged`
- `active_provider() -> IntelligenceProvider | None`

**Tests:**
- Per kind: a round trip that keeps the stored config exactly.
- A blank secret keeps the stored value, and Clear removes it.
- Unknown keys are kept.
- Switching kind drops `api_key`.
- An unsafe `host` gets the field error. A host in `SSRF_ALLOWED_HOSTS` passes.
- A stale save raises `StaleProvider`.
- Turning one on turns the others off. Turning one off leaves the others alone.
- A bound form never renders the stored `api_key`.

Commit `feat(intelligence): the provider form, with write-only secrets`.

---

### Task 4: The API key form and save

**Files:**
- Create: `config/api_keys.py`
- Test: `config/_tests/test_api_keys.py`

`APIKeyForm(forms.ModelForm)` uses the fields `name`, `is_active` and
`allowed_endpoints`:
- `allowed_endpoints` is a `ListField` with a 4-row textarea and the help text "One path
  per line, such as /alerts/webhook/. Leave empty to allow every endpoint."
- Each line must start with `/`. Otherwise the error is "Each line must be a path starting
  with /."
- Labels: "Name" and "On". The "On" help reads "A key that's off is refused."

Also in the module:
- `KEY_FIELDS = ("name", "is_active", "allowed_endpoints")`
- `StaleKey(Stale)`
- `key_digest(row)`, salt `"config.apikey"`
- `save_key(pk, values, *, expected)`
- `endpoints_words(key) -> str`: "All endpoints", or `format_list` of the paths.

**Tests:**
- Round trip.
- Path validation.
- Stale save.
- `endpoints_words` for an empty list and for a set list.
- A save through `save_key` never changes `key` or `prefix`.

Commit `feat(config): the API key form and save`.

---

### Task 5: Intents, permissions and the System nav

**Files:**
- Modify: `apps/console/intents.py`, `templates/console/partials/_system_nav.html`
- Test: `apps/console/_tests/test_intents.py`

Permission constants:
- `PROVIDER_READ = ("intelligence.view_intelligenceprovider",
  "intelligence.change_intelligenceprovider")`
- `PROVIDER_ADD`, `PROVIDER_CHANGE` and `PROVIDER_DELETE`
- `KEY_READ = ("config_app.view_apikey", "config_app.change_apikey")`
- `KEY_ADD`, `KEY_CHANGE` and `KEY_DELETE`

Intents, mirroring the channel ones:
- `view_provider` and `edit_provider`: pk goes to `console:provider`, otherwise to
  `console:providers`.
- `add_provider` and `delete_provider`.
- `view_key`, `edit_key`, `add_key` and `delete_key`.

`edit_provider` stops linking the admin. Every intent whose URL doesn't resolve yet
renders as text until its task adds the URL, as 9a did.

The subnav adds "Providers" and "API keys" tabs, each shown only with its read permission.
The System top-nav item opens the first tab the user can read, and it's hidden when the
user can read none.

**Tests:** each intent for each permission, and the nav for each single-permission reader.

Commit `feat(console): provider and key intents, and their tabs`.

---

### Task 6: The provider list and editor

**Files:**
- Create: `apps/console/queries/providers.py`, `apps/console/views/providers.py`,
  `apps/console/views/provider.py`
- Create: `templates/console/providers.html`, `provider.html`, `provider_new.html`,
  `partials/_providers.html`, `partials/_provider.html`
- Modify: `apps/console/urls.py`, `apps/console/views/__init__.py`,
  `templates/console/not_found.html`
- Test: `apps/console/_tests/queries/test_providers.py`,
  `apps/console/_tests/views/test_providers.py`, `apps/console/_tests/views/test_provider.py`

URLs:
- `providers/` → `console:providers`
- `providers/new/` → `console:provider-new`, a picker without `?provider=`
- `providers/<pk>/` → `console:provider`

**The list:**
- Rows are ordered with the active one first, then by name.
- Each row has a tone icon: ok for the active provider, muted otherwise.
- The name links through `view_provider`, followed by its kind label and "In use" or "Off".
- The meta line gives the description, if any.
- The box header says "Analysis uses <name>.", or "No provider is on, so analysis uses the
  built-in local analyzer."
- "Add a provider" sits in the page head.

**The editor:**
- It mirrors the channel editor, with the switch button "Show this provider's settings".
- When `is_active` is unchecked and another provider is active, a note under "On" reads
  "Turning this on turns off <other>." The note is static text, rendered server side.
- Details holds the provider key, the undeclared setting names, Created and Updated.

**Tests:** mirror `test_channel.py`:
- The stored `api_key` is absent from every response: GET, an invalid POST, the 409, the
  switch and the 400.
- An unsafe host shows the field error.
- Saving with "On" ticked turns the old provider off.

Commit `feat(console): providers, with typed settings per provider`.

---

### Task 7: Delete a provider

Mirror `channel_delete`. `providers/<pk>/delete/` → `console:provider-delete`.

The consequence reads:
- for the active provider: "Analysis falls back to the built-in local analyzer."
- otherwise: "Analysis keeps using <active name>.", or the fallback sentence when none is
  active

Also add `ProviderEditor.delete` and the "Delete provider" link.

Commit `feat(console): delete a provider, saying what analysis falls back to`.

---

### Task 8: API key list, create and editor

**Files:**
- Create: `apps/console/queries/keys.py`, `apps/console/views/keys.py`,
  `apps/console/views/key.py`
- Create: `templates/console/keys.html`, `key.html`, `key_created.html`,
  `partials/_keys.html`, `partials/_key.html`
- Modify: `apps/console/urls.py`, `apps/console/views/__init__.py`, `not_found.html`
- Test: `apps/console/_tests/queries/test_keys.py`, `apps/console/_tests/views/test_keys.py`,
  `apps/console/_tests/views/test_key.py`

URLs:
- `keys/` → `console:keys`
- `keys/new/` → `console:key-new`
- `keys/<pk>/` → `console:key`

**The list:**
- Rows are ordered by name.
- Icon: ok when on, muted when off.
- The name links, followed by "On" or "Off".
- The meta line reads `<prefix>… · <endpoints words> · used <relative time>`, or "never
  used".

**Create:** a POST to `new/` validates `APIKeyForm`, then calls `APIKey.objects.create(...)`
inside `transaction.atomic`. It renders `key_created.html` with status 200 and
`Cache-Control: no-store`:
- a read-only input holding `key._raw_key`
- the text "Copy this key now. It isn't shown again."
- a link back to the key page

It never calls `messages` with the token.

**Edit:** the same shape as the route editor. Name, On and Allowed endpoints, then Details
with the prefix, Created and Last used. The save goes through `save_key`, with a 409 when
stale.

**Tests:**
- The raw token appears in the create response only. It's absent from:
  - the following GET of the list and of the key page
  - the session's message storage, checked through `messages.get_messages`
  - the response cookies
- No-store is set on the create response.
- A double create makes two keys. That pins the known limit in the plan corrections.
- The 403s, the 404 and the 409.

Commit `feat(console): API keys, with a one-time token`.

---

### Task 9: Delete an API key

Mirror `channel_delete`. `keys/<pk>/delete/` → `console:key-delete`.

The consequence reads:
- "Anything still sending this key gets refused. It was last used <relative time>."
- or "It was never used."

Add the "Delete key" link.

Commit `feat(console): delete an API key, saying when it was last used`.

---

### Task 10: Point the readiness row at the console and cut the admins

**Files:**
- Modify: `config/dashboard.py`, where the provider readiness `url` becomes
  `reverse("console:providers")`.
- Modify: `apps/intelligence/admin.py`. `IntelligenceProviderAdmin` becomes stock and
  read-only: `list_display = ["name", "provider", "is_active", "updated_at"]`,
  `exclude = ["config"]`, the three `has_*_permission` methods return `False`, and the
  docstring reads "Read-only. Operators edit providers in the console." Its
  `formfield_overrides` and fieldsets go. `AnalysisRunAdmin` keeps its JSON widget.
- Modify: `config/admin.py`. `APIKeyAdmin` gets the same treatment: `list_display =
  ["name", "masked_key", "is_active", "created_at", "last_used_at"]`, `exclude = ["key"]`,
  read-only, and `save_model` goes.
- Modify: the admin and dashboard tests that pin removed behaviour.

**Tests:**
- Each admin refuses add, change and delete, and its changelist still renders.
- A stored `api_key` is absent from the provider admin change view.
- The key digest is absent from the key admin change view.

Commit `feat!: the provider and API key admins are stock and read-only`.

---

### Task 11: The guard test covers providers and keys

**Files:**
- Modify: `apps/console/_tests/fixtures.py`, `apps/console/_tests/test_guard.py`

Fixtures:
- one provider per `PROVIDER_CHOICES` key, with a stored secret where the kind has one
- the Claude provider active
- one provider whose stored kind the hub doesn't have
- two API keys, one off, one never used, and one with endpoints

`World` gains `provider_pks` and `key_pks`.

Guard URLs:
- the provider list, the picker, `new/?provider=<key>` per kind, every provider page and
  every provider delete page
- the key list, `keys/new/`, every key page and every key delete page

Shown strings:
- "Analysis uses"
- "Turning this on turns off"
- "All endpoints"
- "never used"
- "A provider this hub doesn't have"

Every stored provider secret and every key's raw token is absent from the raw HTML of
every guarded page.

Commit `test(console): the guard covers providers and keys`.

---

### Task 12: Docs

**Files:**
- `AGENTS.md`: convention 3 adds providers and API keys to what's read and acted on in the
  console.
- `apps/console/AGENTS.md`: the System bullet, the provider and key pages, and the
  intents.
- `apps/intelligence/AGENTS.md`: `config_fields`, the guard test, `ProviderForm`,
  `save_provider`, and the admin expectations.
- `apps/notify/AGENTS.md`: `ConfigField` now lives in `config/config_fields.py`.
- Any `docs/` page or README that says to set a provider or API key in Django admin.
  Grep for `intelligenceprovider`, `apikey`, "API Keys" and "Intelligence providers".
  Not `docs/plans/`.

Commit `docs: providers and API keys live in the console`.

{% endraw %}
