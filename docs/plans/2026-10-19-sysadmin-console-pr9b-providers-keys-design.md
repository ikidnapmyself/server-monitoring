---
title: "Sysadmin Console PR 9b: Providers and API Keys Design"
parent: Plans
---

# Sysadmin console PR 9b: providers and API keys

The second half of domain slice 9 in `2026-10-15-sysadmin-console-foundation-design.md`.
9a (`2026-10-18-sysadmin-console-pr9a-routing-design.md`) moved routes and channels. 9b
moves AI providers and API keys to the console's System section and cuts
`IntelligenceProviderAdmin` and `APIKeyAdmin` to stock and read-only.

## What exists

- `IntelligenceProvider` (`apps/intelligence/models.py`): `name`, `provider`, `config`,
  `is_active` and `description`. Saving an active row turns every other row off.
- `get_active_provider` (`apps/intelligence/providers/__init__.py`) builds
  `PROVIDERS[row.provider](**row.config)`, so the config keys are constructor kwargs:
  - every AI provider takes `api_key`, `model`, `max_tokens` and `timeout_s`
  - Copilot and Grok also take `base_url`, and Ollama takes `host`
  - local takes `top_n_processes`, `large_file_threshold_mb`, `old_file_days` and
    `scan_paths`
- `APIKey` (`config/models.py`) stores a SHA-256 of the token, an 8-character `prefix`,
  `name`, `is_active`, `last_used_at` and `allowed_endpoints`. The raw token exists only
  as `_raw_key` right after the first save.
- `APIKeyAdmin` shows the raw token once, in a warning message.
- `IntelligenceProviderAdmin` renders `config`, `api_key` included, in a JSON widget.
- 9a's channel work: `ConfigField`, write-only secrets, the lossless round trip, the
  digest stale-save check, the guard test and the console page patterns.
- The intents `edit_provider` and the readiness provider row, both of which still link
  the admin changelist.

## Pages

### `/console/providers/`

Name, provider label, Active or Inactive, and which provider the pipeline uses now. With
none active the page says analysis falls back to the built-in local analyzer.

### `/console/providers/new/`, `?provider=<key>` and `/console/providers/<pk>/`

A picker, then name, description, active, and the provider's typed field group. Turning a
provider on warns that it turns off the one that's on now. The model already does the
switch on save.

### `/console/providers/<pk>/delete/`

A confirm page. Deleting the active provider says analysis falls back to the local
analyzer.

### `/console/keys/`

Name, the first 8 characters followed by `…`, Active or Off, when it was last used, and
the allowed endpoints in words, or "All endpoints" when the list is empty.

### `/console/keys/new/` and `/console/keys/<pk>/`

Name, active, and allowed endpoints, one path prefix per line. Creating a key renders the
raw token once, on the response page. It never goes through `messages`, since cookie
message storage would put it in a cookie. The key can't be edited, and there's no
rotation. Turning a key off revokes it.

### `/console/keys/<pk>/delete/`

A confirm page that says when the key was last used.

## Typed provider forms

`ConfigField` moves from `apps/notify/config_fields.py` to `config/config_fields.py`, a
shared utility. Notify imports it from there, and `apps.intelligence` never imports
`apps.notify`.

`BaseProvider` gains `label`, `config_fields` and `all_config_fields()`.

| Provider | Fields (secret marked *) |
|---|---|
| claude, openai, gemini, mistral | api_key*, model, max_tokens, timeout_s |
| grok, copilot | api_key*, model, max_tokens, timeout_s, base_url |
| ollama | model, max_tokens, timeout_s, host |
| local | top_n_processes, large_file_threshold_mb, old_file_days, scan_paths |

`ProviderForm` mirrors `ChannelForm`:
- Secrets are write-only.
- Unknown keys are kept and named in Details.
- Changing the provider drops the old provider's secrets.
- `base_url` and `host` go through `validate_safe_url` with `SSRF_ALLOWED_HOSTS`, the same
  check the provider makes.

A guard test fails when a provider reads a config key it doesn't declare.

## Stale saves

Providers use a salted HMAC digest of the row, as channels do, with the same 409. API
keys use the same digest over `name`, `is_active` and `allowed_endpoints`.

## Cut to stock

`IntelligenceProviderAdmin` and `APIKeyAdmin` become stock and read-only, and both exclude
their secret-bearing fields. `edit_provider` and the readiness provider row point at
`console:providers`. `AnalysisRunAdmin` keeps the JSON widget until PR 10.

Reads need `view_*` or `change_*`. Writes need `add_*`, `change_*` or `delete_*`, as Django
assigns them.

## Tests

- Per provider: form round trip, secret keep, secret clear, unknown keys kept.
- An unsafe `base_url` or `host` is refused with a field error.
- Turning a provider on shows the warning, and after saving only that one is active.
- Create a key: the raw token shows on that response only. It's absent from the list, the
  key page, the message cookie and the admin.
- Stale save 409 for both.
- Delete consequences for both.
- The console guard covers the new pages.
- 403 for each missing permission.
- 100% branch coverage on changed code.

## Out of scope

- The preflight readiness row, `view_preflight`, `view_check_run` and
  `view_preflight_run`. Those belong to reports, in PR 10.
- Key rotation.
- An audit trail for console writes. That's one follow-up covering every console page.
