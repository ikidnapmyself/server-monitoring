---
title: Templates
layout: default
nav_order: 6
---

# Templates

The notification system uses [Jinja2](https://jinja.palletsprojects.com/) templates to format messages for each delivery driver. Every notification — whether it goes to Slack, email, PagerDuty, or a generic webhook — is rendered through a template before dispatch.

Templates live in `apps/notify/templates/` as `.j2` files. If Jinja2 is not installed, the system falls back to Python's `str.format_map` for basic `{variable}` substitution, but all built-in templates require Jinja2.

## How Templates Are Resolved

When a driver renders a notification, the system looks for a template in this order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Config key (`template`, `text_template`, or `payload_template`) | `{"template": "file:my_custom.j2"}` |
| 2 | Default driver file: `<driver>_text.j2` | `slack_text.j2` |
| 3 | Default driver file: `<driver>_payload.j2` | `generic_payload.j2` |
| 4 | Default driver file: `<driver>.j2` | `slack.j2` |

For HTML (email only), the system checks `html_template` in config, then falls back to `<driver>_html.j2`. HTML is optional — if no HTML template is found, only the text version is sent.

### Template Spec Formats

The `template` config value accepts several formats:

{% raw %}
```python
# File reference (explicit)
"file:slack_text.j2"

# File reference (auto-detected — if a matching file exists in templates/)
"slack_text.j2"

# Inline template string
"Alert: {{ title }} — Severity: {{ severity }}"

# Dict spec
{"type": "file", "template": "my_custom.j2"}
{"type": "inline", "template": "Alert: {{ title }}"}
```
{% endraw %}

If a string matches an existing file in `apps/notify/templates/`, it is treated as a file reference automatically. Otherwise it is rendered as an inline template.

## Available Templates

Each built-in template renders a pipeline notification from the describers' output, and falls
back to `title` and `message` for a message that didn't come from a pipeline run, such as one
sent through `/notify/send/` or `test_notify`. The trace ID is the only identifier a message
shows, in its footer.

### email_html.j2

HTML email with a severity-colored left border and badge. Escapes every value with `| e`.

**Sections:** title and severity, the incident's detail and `Do this:` action, failing checks,
the AI analysis outcome with its action, a link to the console, and the trace footer.

**Severity colors:** critical = red, warning = yellow, info = teal, success = green.

### email_text.j2

Plain-text email: title, severity, the message body and the trace footer.

### slack_text.j2

Slack Block Kit JSON: a title block with the severity, the message body and a context block
with the trace ID. Escapes `<`, `>` and `&`, which Slack's mrkdwn treats as control characters,
converts `**bold**` to `*bold*`, and truncates a section before Slack's 3000 character limit.

### generic_payload.j2

JSON for generic webhooks: `title`, `message`, `severity`, `channel`, `incident`, `findings`,
`analysis`, `console_url`, `trace_id` and `generated_at`.

### pagerduty_payload.j2

PagerDuty Events API v2 `payload`: `summary` is the title, plus `severity`, `source`,
`component`, `group`, `class`, and `custom_details` with the message and trace ID. The driver
adds the console link to the event's `links` when `CONSOLE_URL` is set.

### incident_notification.j2

Markdown: a heading with the title, severity, the message body and the trace footer.

## Context Variables

Every template receives the same context, built by
`NotificationTemplatingService.build_template_context()` from the notification message.

| Variable | Type | Description |
|----------|------|-------------|
| `title` | string | `[SEVERITY] <problem> on <host>`, or `[RESOLVED] ...` once settled |
| `message` | string | Plain-text body: detail, `Do this:`, failing checks, AI analysis, console link |
| `severity` | string | One of: `critical`, `warning`, `info`, `success` |
| `channel` | string | Notification channel name |
| `tags` | dict | `trace_id`, `run_id` and `idempotency_key` for a pipeline run |
| `trace_id` | string | The run's trace ID, empty when there is none |
| `incident` | description or None | What's wrong, from `describe_incident` |
| `findings` | list of descriptions | Failing checks, worst first, from `describe_findings` |
| `analysis` | description or None | The AI analysis outcome, from `describe_analysis` |
| `console_url` | string | The incident's console page, empty when `CONSOLE_URL` is unset |
| `generated_at` | string | ISO 8601 UTC timestamp |

### Descriptions

`incident`, each item of `findings`, and `analysis` share one shape:

| Field | Type | Description |
|-------|------|-------------|
| `headline` | string | What happened, in a sysadmin's words |
| `detail` | string | More on it, may be empty |
| `tone` | string | `critical`, `warning`, `info`, `ok` or `muted` |
| `action` | string | What to do about it, may be empty |

`incident` and `analysis` are null and `findings` is empty for messages that didn't come from a
pipeline run.

## Customizing Templates

### Override via NotificationChannel Config

Set a custom template in the channel's `config` field in Django admin (`/admin/notify/notificationchannel/`):

```json
{
  "webhook_url": "https://hooks.slack.com/...",
  "template": "file:my_slack.j2"
}
```

Or use an inline template directly:

{% raw %}
```json
{
  "webhook_url": "https://example.com/webhook",
  "template": "ALERT: {{ title }} [{{ severity | upper }}] — {{ message }}"
}
```
{% endraw %}

For email drivers, you can set both text and HTML templates:

```json
{
  "smtp_host": "smtp.example.com",
  "text_template": "file:my_email_text.j2",
  "html_template": "file:my_email_html.j2"
}
```

### Override via NotificationChannel config

Set a `template` key on the channel's `config` (Django Admin → Notify → Notification
channels). The notify stage reads it from the DB-stored channel config:

```json
{
  "webhook_url": "https://hooks.slack.com/services/...",
  "template": "file:my_slack.j2"
}
```

## Writing Custom Templates

### File Location

Place custom templates in `apps/notify/templates/`. The file must have a `.j2` extension (or be referenced with the exact filename).

### Minimal Example

{% raw %}
```jinja2
{# my_webhook.j2 — minimal JSON webhook payload #}
{
  "text": {{ title | tojson }},
  "level": "{{ severity | upper }}",
  "body": {{ message | tojson }}
}
```
{% endraw %}

### Accessing Nested Data Safely

Guard on the description before reading its fields, so the template still renders a bare
message:

{% raw %}
```jinja2
{% if incident %}
  {{ incident.headline }}
  {% if incident.action %}Do this: {{ incident.action }}{% endif %}
  {% for f in findings[:5] %}
    - {{ f.headline }}
  {% endfor %}
{% else %}
  {{ message }}
{% endif %}
```
{% endraw %}

### Producing JSON Output

When your template outputs JSON (for Slack, PagerDuty, or webhook payloads), always use `| tojson` to escape strings:

{% raw %}
```jinja2
{# CORRECT — tojson handles quoting and escaping #}
{"title": {{ title | tojson }}, "severity": {{ severity | tojson }}}

{# WRONG — breaks if title contains quotes or special characters #}
{"title": "{{ title }}", "severity": "{{ severity }}"}
```
{% endraw %}

### HTML Templates

For HTML output, use `| e` to escape user content and prevent XSS:

{% raw %}
```jinja2
<h2>{{ title | e }}</h2>
<p>{{ message | e }}</p>
```
{% endraw %}

## Jinja2 Quick Reference

Filters and patterns used across the built-in templates:

{% raw %}
| Pattern | Usage |
|---------|-------|
| `{{ var | upper }}` | Uppercase string |
| `{{ var | tojson }}` | JSON-safe encoding (adds quotes, escapes) |
| `{{ var | e }}` | HTML entity escaping |
| `{{ var | default('fallback') }}` | Default for missing/falsy values |
| `{{ var | trim }}` | Strip whitespace |
| `{% if var %}...{% endif %}` | Conditional block |
| `{% for item in list %}...{% endfor %}` | Loop |
| `{% for k, v in dict.items() %}` | Dict iteration |
| `{{ list[:5] }}` | Slice (limit items) |
| `{% if var is mapping %}` | Type check (dict) |
| `{% if var is string %}` | Type check (string) |
| `{% if var is defined %}` | Check if variable exists |
| `{% set x = 'value' %}` | Variable assignment |
| `{% macro name(arg) %}...{% endmacro %}` | Reusable macro |
| `{{ loop.last }}` | True on last loop iteration |
| `{# comment #}` | Template comment (not in output) |
{% endraw %}