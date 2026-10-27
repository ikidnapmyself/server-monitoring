# apps.notify — Agent Notes

This file contains **app-local** guidance for working in `apps/notify/`.

## Role in the pipeline

Stage: **communicate**

Responsibilities:
- Render and dispatch notifications through configured channels/drivers
- Normalize provider responses for orchestration to persist

Output contract (to orchestrator):
- `{ deliveries: [...], provider_ids, notify_output_ref }`

**A delivery says why it failed.** Each delivery dict carries `failure`, empty on success
and otherwise one of `config.failures.FAILURE_KINDS`. A driver reports what it knows in
its result: `status_code` for an HTTP error, or `failure` when it can name the kind, as
email does for an SMTP auth error. The base class's `_handle_*` helpers fill these in.
`NotifyExecutor` takes a reported `failure` first, then classifies `status_code`, and
falls back to `rejected`. Every driver declares a display `label` ("Email", "Slack").
`describe_delivery` and `describe_notify` in `apps/notify/describing.py` read the stored
deliveries and use that label in the headline, such as "Not sent to #ops: Slack is rate
limiting this hub", with the HTTP status and error in Details.

**Content sourcing.** `NotifyExecutor` builds every message with
`apps.orchestration.notification.build_notification(incident, previous, trace_id)`. The
title and severity come from the incident: `[<SEVERITY>] <describe_incident headline>`, or
the status (`[RESOLVED]`, `[ACKNOWLEDGED]`, `[CLOSED]`) once it is settled. The body and the
message context come from `describe_incident`, `describe_findings` (the diagnose snapshot) and
`describe_analysis` (the analyze snapshot), each flattened to
`{headline, detail, tone, action}` with facts and intents left behind. **Intelligence never
sets the title or severity** and never suppresses a notification. A failed analysis says so in
the body, so a checkers-only node with no AI still sends a useful, correctly-severe alert.
`CONSOLE_URL` adds a link to the incident's console page. Messages carry the trace ID in the
footer and no other identifier. Notify stays dumb: it renders what it was handed.

**Channel selection (`NotifySelector.resolve`).** Priority: a named `provider_arg`
matching an active `NotificationChannel` wins; otherwise the argument is treated as a
provider key (`slack`, `generic`, …) against payload-supplied config. **The
"first active channel ordered by name" default is opt-in** — `resolve` selects it only
for callers passing `allow_default_channel=True`. The two interactive callers opt in
(`views.py`'s send endpoint and `manage.py test_notify`): an operator asking for a send
means "use my channel". **The pipeline does not**, and must not be changed to: picking a
channel by alphabetical accident is how a critical alert lands in `#aaa-general`.

For a pipeline run, delivery has exactly one source of truth:
`PipelineDefinition.routed_channel()` — the lane's `channel` FK, and only while that
channel is active. Never re-derive "active" and never read the FK directly. A lane that
routes to NOTIFY without one **fails** (`no_channel`, non-retryably) rather than
defaulting to another channel: the failure this introduces is loud and fixable, the one
it replaces was silent delivery to the wrong place. A hub with no channel at all is not
misconfigured — its lanes simply do not list `notify` (see
`apps/orchestration/AGENTS.md` and
`docs/plans/2026-08-22-lane-channel-required-design.md`). A lane whose channel names a
driver absent from `DRIVER_REGISTRY` fails the same way as `no_driver`.

## Key modules

- `apps/notify/drivers/` — notification drivers/backends
- `apps/notify/config_fields.py`: `COMMON_FIELDS` and the shared field builders. `ConfigField` itself, one setting a driver reads from its channel config, lives in `config/config_fields.py`
- `apps/notify/forms.py`: `ChannelForm`, the console's channel editor
- `apps/notify/channels.py`: `save_channel`, `channel_digest`, `StaleChannel` and `lanes_cut_by_delete`
- `apps/notify/describing.py` — `describe_delivery`, `describe_notify`
- `apps/notify/models.py` — notification channel configuration models
- `apps/notify/urls.py` — URL routing
- `apps/notify/management/commands/test_notify.py` — interactive test notification wizard

## Channel settings

**A driver declares every setting it reads.** Each driver lists its `ConfigField`s in
`config_fields`. `BaseNotifyDriver.all_config_fields()` appends `COMMON_FIELDS`, the
destination and template settings every driver shares. `credential_aliases` maps an old
key to the declared one it stands for, such as the generic driver's `webhook_url` for
`endpoint`. `ChannelForm` builds the console form from these declarations alone.
`_tests/test_config_fields.py` scans each driver's source and fails when it reads a
config key that is neither declared nor listed as an alias there.

**Secrets are write-only.** A field with `secret=True` never renders its stored value.
The input stays empty and says "Set. Leave blank to keep it." A blank submit keeps the
stored value, and "Clear" removes it and any alias. Switching driver drops the old
driver's secrets and aliases. A headers field must be secret.

**Unknown keys are kept.** A stored key the driver doesn't declare survives every save.
The page names it in Details and never shows its value.

**A save is checked against what was opened.** The form posts `channel_digest`, an HMAC
of the stored row keyed on the secret key, so the page carries no plain hash of
credentials. `save_channel` writes only when the row still hashes to it, and raises
`StaleChannel` otherwise. `lanes_cut_by_delete` lists the lanes on a channel that notify,
which a delete leaves with no channel.

## Management command: test_notify

Default mode is an **interactive wizard** that discovers active `NotificationChannel` records,
prompts for message options, and provides a retry/switch loop. Use `--non-interactive` for
CI/scripting (flag-based behavior).

Key contract:
- `_handle_interactive()` — wizard entry point, uses `_select_channel()`, `_prompt_message_options()`, `_send_and_show_result()`, `_post_send_loop()`
- `_handle_non_interactive()` — original flag-based behavior, uses `NotifySelector.resolve()` for channel/driver resolution
- `DRIVER_REGISTRY` — maps driver names to driver classes (email, slack, pagerduty, generic)
- Interactive mode uses `builtins.input()` — tests mock this with `side_effect` lists

## Boundary rules

- Avoid duplicate notifications:
  - Use **idempotency keys** for outbound provider calls where applicable.
- Always set timeouts/retries/backoff for outbound HTTP calls.
- Do not log secrets (tokens/webhook URLs). Prefer refs/redaction.

## Admin

Operators read and edit channels in the console (`/console/channels/`). At `/admin/`, which
only active superusers reach, `NotificationChannelAdmin` excludes `config`, refuses add and
shows `driver` read-only, so no stored setting renders or can be filtered on there.
Deliveries are read on the console run page.

## App layout rules (required)

- Endpoints must live under `apps/notify/views/` as modules (one endpoint/module per file).
  - Examples: `views/send.py`, `views/drivers.py`, `views/batch.py`
  - Prefer small modules (one view function or class per file) to simplify imports and testing.
- Tests must live under `apps/notify/_tests/` and mirror the module tree in `views/` and `drivers/`.
  - Examples:
    - `apps/notify/drivers/slack.py` → `apps/notify/_tests/drivers/test_slack.py`
    - `apps/notify/views/send.py` → `apps/notify/_tests/views/test_send.py`
  - Test files should be discoverable by pytest (use `test_*.py` or `*_tests.py` naming — current `pyproject.toml` supports these).
- Fixtures, shared helpers, and package-level test utilities belong in `apps/notify/_tests/conftest.py` or `apps/notify/_tests/_helpers/`.

## Doc vs code status

Tests have been migrated to `_tests/` (completed). Some code still uses monolithic `views.py`; migrate to `views/` package when touching related code.

## Templates and presentation (added)

This app uses a template-driven approach for notification formatting. Templates live under `apps/notify/templates/` and are used to render driver-specific text, HTML, or payloads.

Key points:
- Preferred engine: Jinja2 (recommended). A very small Python fallback exists for simple `{name}` style formatting but Jinja features require Jinja2 installed in your runtime.
- Template specs supported:
  - Inline string (Jinja or Python format style)
  - File reference: `"file:slack_text.j2"` or `{"type":"file","template":"slack_text.j2"}`
- Config keys drivers will check (in `NotificationChannel.config` or payload `notify_config`):
  - `template` / `text_template` — plain/text template
  - `html_template` — HTML template (email)
  - `payload_template` — payload-oriented template (JSON/text)

Driver default file naming (search order):
1. `<driver>_text.j2` (e.g. `slack_text.j2`)
2. `<driver>_payload.j2`
3. `<driver>.j2`

Templates receive one context, built by `NotificationTemplatingService.build_template_context()`
from the message:
- `title`, `message`, `severity`, `channel`, `tags`, `trace_id`, `generated_at`
- `incident` and `analysis` (a description or None) and `findings` (a list of descriptions),
  each `{headline, detail, tone, action}`
- `console_url`, empty when `CONSOLE_URL` is unset

A message that didn't come from a pipeline run (`/notify/send/`, `test_notify`, PagerDuty
acknowledge/resolve) has no descriptions, so every template falls back to `title` and
`message`. Don't reintroduce raw stage summaries, host metrics read with psutil, or dumps of
`tags` and `context` into a template.

Example `email_text.j2`-style fragment:

```
{{ title }}
{% if incident and incident.action %}Do this: {{ incident.action }}{% endif %}
{% for f in findings %}- {{ f.headline }}
{% endfor %}
```

## Base driver helpers and conventions (added)

To avoid duplication across drivers, use the `BaseNotifyDriver` helpers:

- `_template_context(message)` — builds the context passed to templates.
- `_render_message_templates(message, config)` — finds and renders the text/html template for the driver (config first, then driver-default files). This method enforces template presence and raises when none is found.
- `_prepare_notification(message, config)` — high-level helper that returns a dict: `{ "text", "html", "payload_obj", "payload_raw" }`.

Guidelines for drivers:
- Call `prepared = self._prepare_notification(message, config)` near the start of `send()`.
- Use `prepared['text']` and `prepared['html']` for human-facing bodies and `prepared['payload_obj']` (if present) for structured JSON payloads.
- Keep transport logic (HTTP, auth, retries) inside the driver; keep formatting in templates.

## How to extend with a new driver (added)

1. Create `apps/notify/drivers/mydriver.py` and subclass `BaseNotifyDriver`.
   Set `label` and declare every config key it reads in `config_fields`. Mark credentials `secret=True`.
2. Implement `validate_config(config)`.
3. Implement `send(message, config)` — recommended pattern:

```python
from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

class MyDriver(BaseNotifyDriver):
    name = "mydriver"

    def validate_config(self, config):
        return "api_key" in config

    def send(self, message: NotificationMessage, config: dict):
        prepared = self._prepare_notification(message, config)
        # Use prepared['payload_obj'] if provided by template; otherwise prepared['text']
        payload = prepared.get('payload_obj') or { 'title': message.title, 'message': prepared.get('text') }
        # perform API call and return normalized dict
        return {"success": True, "message_id": "..."}
```

Notes:
- Templates are required for drivers by default; if a driver lacks a configured template and no default template file exists, `BaseNotifyDriver` will raise a `ValueError` to fail fast and force configuration.
- If you prefer softer behavior, we can change this to log a warning and fall back to a multi-line text format.

## Testing templates locally (added)

You can render templates locally using the templating helper:

```bash
uv run - <<'PY'
from apps.notify.templating import render_template
ctx = {
  'title': 'Test Alert', 'severity': 'info', 'message': 'This is a test',
  'intelligence': {}, 'recommendations': [], 'incident_id': None, 'source': None
}
print(render_template('file:slack_text.j2', ctx))
PY
```

If rendering raises a message about Jinja2 syntax, install Jinja2 in the environment:

## Troubleshooting & operational notes (added)

- If you see `ValueError: No template found for driver '...'`, add a template file (e.g. `apps/notify/templates/<driver>_text.j2`) or set `template`/`payload_template` in the `NotificationChannel.config` for that channel.
- Keep templates under version control and avoid embedding secrets inside templates.

## Security standards (audit-enforced)

Authoritative source: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/notify/` section. This module is clean (no findings) but carries three high-leverage rules: outbound HTTP must be SSRF-checked, templates must be sandboxed, and config-supplied template source must never come from an API payload.

### Rules for new outbound HTTP drivers
- **`safe_urlopen` from `config.security.http` is the only allowed urlopen path.** Raw `urllib.request` is banned by ruff `TID251`. Pass `allowed_hosts=settings.SSRF_ALLOWED_HOSTS` to every call so operators can configure exceptions.
- **Redirect handling re-validates each target.** `safe_urlopen` does this for you via `_SSRFRedirectHandler` — do not roll your own redirect handler.
- **Hardcoded URL constants are preferred** when the destination is fixed (see PagerDuty driver). No URL-injection class if there is no URL to inject.
- **Generic-driver response-body echo:** the generic driver returns the upstream response body to the API caller. This makes `SSRF_ALLOWED_HOSTS` the **gating** control, not a defense-in-depth layer. Keep that allowlist narrow; do not loosen it in code.
- **Slack URL prefix check** (`https://hooks.slack.com/`) blocks userinfo-host smuggling. Trailing slash is intentional and load-bearing.

### Template handling (SSTI prevention)
- **Templates render in `jinja2.sandbox.ImmutableSandboxedEnvironment`** — never the default `Environment`. The sandbox blocks `__class__`, `__mro__`, `__subclasses__` and the standard SSTI gadgets. Regression tests in `apps/notify/_tests/` cover these.
- **DB-stored template names are routed through `resolve_safe_name`** (`apps/notify/templating.py`). Path traversal on filenames is closed.
- **Bare-string Jinja syntax (`{{`, `{%`, `{#`) is explicitly rejected** by `templating.py:115-125` when used as a template *name* (not a body). This is a deliberate rejection of attempts to smuggle Jinja source through template-name fields.
- **Pipeline payload `template` keys are stripped** by `apps/orchestration/executors.py:_PAYLOAD_TEMPLATE_KEYS` before reaching `NotifySelector.resolve()`. Templates may originate only from on-disk files or DB `NotificationChannel.config` (staff-auth gated). If you add a new template-bearing config key, **add it to `_PAYLOAD_TEMPLATE_KEYS`**.

### Email driver
- `email.header.Header.encode()` raises on CRLF-embedded values — this is the email-header-injection mitigation. Do not bypass it by building headers as plain strings.
- SMTP host is not URL-shaped; no `validate_safe_url` equivalent for it. Trust comes from admin-configured `NotificationChannel.config`.

### Logging rules
- **No `channel.config` in logs.** It contains secrets (SMTP passwords, integration keys, webhook URLs). Log channel `name` and `driver` only.
- **Log remote `error_body` strings, not request bodies.** Upstream error responses are useful for ops; request bodies may contain payload-derived secrets.
- **Log endpoint URLs but not query strings.** Webhook tokens sometimes live in query strings.

### Payload construction
- **Outbound JSON via `json.dumps`** — never string concatenation. Templates can use the `|tojson` filter for inline field interpolation.

### Audit checks before merging
- [ ] New driver: uses `safe_urlopen` (or hardcoded constant URL); calls `validate_config(config)` before `send()`.
- [ ] New template-bearing config key: added to `apps.orchestration.executors._PAYLOAD_TEMPLATE_KEYS` so it cannot be supplied from API payloads.
- [ ] No `logger.*` call includes `config`, `webhook_url`, `api_key`, or `routing_key`.
- [ ] Templates render in `ImmutableSandboxedEnvironment` only — no `jinja2.Environment` import in driver code.
- [ ] Run `uv run pytest apps/notify/_tests/` and confirm SSTI sandbox-bypass regression tests still pass.
