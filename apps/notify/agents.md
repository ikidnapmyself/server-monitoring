# apps.notify — Agent Notes

This file contains **app-local** guidance for working in `apps/notify/`.

## Role in the pipeline

Stage: **communicate**

Responsibilities:
- Render and dispatch notifications through configured channels/drivers
- Normalize provider responses for orchestration to persist

Output contract (to orchestrator):
- `{ deliveries: [...], provider_ids, notify_output_ref }`

## Key modules

- `apps/notify/drivers/` — notification drivers/backends
- `apps/notify/models.py` — notification channel configuration models
- `apps/notify/urls.py` — URL routing

## Boundary rules

- Avoid duplicate notifications:
  - Use **idempotency keys** for outbound provider calls where applicable.
- Always set timeouts/retries/backoff for outbound HTTP calls.
- Do not log secrets (tokens/webhook URLs). Prefer refs/redaction.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.notify`, admin should make it easy to:
- Manage `NotificationChannel` configurations safely (mask/redact secrets)
- Inspect notification attempts/deliveries for pipeline runs (typically via orchestration models)
- Correlate deliveries with `Incident` and `trace_id/run_id` to debug duplicates/failures

## App layout rules (required)

- Endpoints must live under `apps/notify/views/` (endpoint/module-based).
  - Example: `views/send.py`, `views/drivers.py`, `views/batch.py`
- Tests must live under `apps/notify/tests/` and mirror the module tree.
  - Example: `drivers/slack.py` → `tests/drivers/test_slack.py`
  - Example: `views/send.py` → `tests/views/test_send.py`

## Doc vs code status

Some code still uses `views.py` / `tests.py`. This doc defines the **target layout** going forward.

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

Templates receive the following top-level context:
- `title`, `message`, `severity`, `channel`, `tags`, `context`
- `incident` — structured dict with metrics and summaries (see below)
- Convenience aliases: `intelligence`, `recommendations`, `incident_id`, `source`

`incident` contains (selected keys):
- `cpu_count`, `ram_total_human`, `disk_total_human`
- `ingest`, `check`, `intelligence` (stage summaries)
- `recommendations` (structured) and `recommendations_pretty` (human-readable)
- `incident_id`, `source`, `environment`, `generated_at`

Example (short) `slack_text.j2` fragment:

```
*{{ title }}* — `{{ severity | upper }}`

{{ intelligence.summary or (recommendations[0].description if recommendations else message) }}

Incident: `{{ incident_id }}` | Source: `{{ source }}`
```

## Base driver helpers and conventions (added)

To avoid duplication across drivers, use the `BaseNotifyDriver` helpers:

- `_compose_incident_details(message, config)` — builds the `incident` dict with metrics and summaries.
- `_template_context(message, incident)` — builds the context passed to templates.
- `_render_message_templates(message, config)` — finds and renders the text/html template for the driver (config first, then driver-default files). This method enforces template presence and raises when none is found.
- `_prepare_notification(message, config)` — high-level helper that returns a dict: `{ "incident", "text", "html", "payload_obj", "payload_raw" }`.

Guidelines for drivers:
- Call `prepared = self._prepare_notification(message, config)` near the start of `send()`.
- Use `prepared['text']` and `prepared['html']` for human-facing bodies and `prepared['payload_obj']` (if present) for structured JSON payloads.
- Keep transport logic (HTTP, auth, retries) inside the driver; keep formatting in templates.

## How to extend with a new driver (added)

1. Create `apps/notify/drivers/mydriver.py` and subclass `BaseNotifyDriver`.
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
