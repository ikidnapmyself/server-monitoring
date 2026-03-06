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

### email_html.j2

HTML email with severity-colored left border and badge. Escapes all user content with `| e` for XSS safety.

**Sections:** Title with color-coded severity badge, incident ID, source/timestamp, message body, intelligence summary, probable cause, recommended actions, recommendations (up to 10), tags, context, ingest summary, check summary.

**Severity colors:** critical = red, warning = yellow, info = teal, success = green.

### email_text.j2

Plain-text email fallback. Same sections as the HTML template in a readable text format. Uses `| upper` for severity, `| default()` for safe recommendation access.

### slack_text.j2

Slack Block Kit JSON payload. Includes a `sanitize` macro that escapes backticks, converts `**bold**` to Slack's `*bold*` syntax, strips carriage returns, and truncates long content.

**Body selection logic:** Prefers `intelligence.summary` or `intelligence.probable_cause` over raw `message` when intelligence data is available.

**Recommendations:** Capped at 5, rendered as bullet points with bold titles.

### generic_payload.j2

Structured JSON for generic webhooks. Uses `| tojson` filters throughout for safe JSON encoding. Includes recommendations (up to 10) and conditional intelligence fields.

### pagerduty_payload.j2

PagerDuty Events API v2 format. Populates `summary`, `severity`, `source`, `component`, `group`, `class`, and `custom_details`. Conditionally includes system metrics (`cpu_count`, `ram_total_human`, `disk_total_human`) when available.

### incident_notification.j2

Markdown-formatted notification. Uses headings, bold, code spans, and blockquote formatting for recommendations. Includes ingest and check summaries with "N/A" fallback when missing.

## Context Variables

Every template receives the same context. The system builds this from the notification message and composed incident details.

### Top-Level Variables

| Variable | Type | Description |
|----------|------|-------------|
| `title` | string | Alert title |
| `message` | string | Alert message body |
| `severity` | string | One of: `critical`, `warning`, `info`, `success` |
| `channel` | string | Notification channel name |
| `tags` | dict | Key-value metadata (source, component, group, etc.) |
| `context` | dict | Full context from the pipeline (check results, raw data) |
| `incident` | dict | Composed incident details (see below) |
| `intelligence` | dict or None | AI analysis results |
| `recommendations` | list or None | Normalized recommendation list |
| `incident_id` | string or None | Incident identifier |
| `source` | string or None | Alert source system |

### The `incident` Object

Composed by `NotificationTemplatingService.compose_incident_details()` from message data and live system metrics:

| Field | Type | Description |
|-------|------|-------------|
| `incident.title` | string | Alert title |
| `incident.message` | string | Alert message |
| `incident.severity` | string | Severity level |
| `incident.channel` | string | Channel name |
| `incident.tags` | dict | Tags from alert |
| `incident.context` | dict | Full context dict |
| `incident.cpu_count` | int or None | CPU count (from context or psutil) |
| `incident.ram_total_bytes` | int or None | Total RAM in bytes |
| `incident.ram_total_human` | string or None | Total RAM formatted (e.g., "16.0 GB") |
| `incident.disk_total_bytes` | int or None | Total disk in bytes |
| `incident.disk_total_human` | string or None | Total disk formatted (e.g., "500.1 GB") |
| `incident.incident_id` | string or None | Incident ID |
| `incident.source` | string or None | Source system |
| `incident.environment` | string or None | Environment name |
| `incident.ingest` | dict or None | Ingest stage summary |
| `incident.check` | dict or None | Check stage summary |
| `incident.intelligence` | dict or None | Intelligence stage results |
| `incident.recommendations` | list | Normalized recommendations |
| `incident.recommendations_pretty` | string | Pretty-printed recommendations text |
| `incident.generated_at` | string | ISO 8601 UTC timestamp |

### The `intelligence` Object

When AI analysis runs, `intelligence` contains:

| Field | Type | Description |
|-------|------|-------------|
| `intelligence.summary` | string | One-line analysis summary |
| `intelligence.probable_cause` | string | Root cause assessment |
| `intelligence.actions` | list[string] | Recommended actions |
| `intelligence.recommendations` | list[dict] | Detailed recommendations |

### The `recommendations` List

Each item in `recommendations` can be a dict or a plain string. Dict items typically contain:

| Field | Type | Description |
|-------|------|-------------|
| `r.title` | string | Short recommendation title |
| `r.description` | string | Detailed explanation |
| `r.priority` | string | Priority level (used in incident_notification.j2) |
| `r.name` | string | Alternative to title (used in slack_text.j2) |
| `r.detail` | string | Alternative to description (used in slack_text.j2) |

The system normalizes recommendations from various formats (dict-of-dicts, single dict, scalar) into a list before passing to templates.

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

### Override via Pipeline Definition

In definition-based pipelines, pass template config in the notify node:

```json
{
  "id": "notify",
  "type": "notify",
  "config": {
    "drivers": ["slack"],
    "template": "file:my_slack.j2"
  }
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

Use Jinja2's `is mapping`, `is defined`, and `| default()` to guard against missing data:

{% raw %}
```jinja2
{# Safe intelligence access #}
{% if intelligence is mapping and intelligence.summary %}
  Summary: {{ intelligence.summary }}
{% endif %}

{# Safe recommendation iteration with limit #}
{% if recommendations %}
  {% for r in recommendations[:5] %}
    - {{ r.title | default('Untitled') }}: {{ r.description | default('') }}
  {% endfor %}
{% endif %}

{# Default values for optional fields #}
Source: {{ source | default('unknown') }}
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

| Pattern | Usage |
|---------|-------|
| `{{ "{{ var \\| upper }}" }}` | Uppercase string |
| `{{ "{{ var \\| tojson }}" }}` | JSON-safe encoding (adds quotes, escapes) |
| `{{ "{{ var \\| e }}" }}` | HTML entity escaping |
| `{{ "{{ var \\| default('fallback') }}" }}` | Default for missing/falsy values |
| `{{ "{{ var \\| trim }}" }}` | Strip whitespace |
| `{% raw %}{% if var %}...{% endif %}{% endraw %}` | Conditional block |
| `{% raw %}{% for item in list %}...{% endfor %}{% endraw %}` | Loop |
| `{% raw %}{% for k, v in dict.items() %}{% endraw %}` | Dict iteration |
| `{% raw %}{{ list[:5] }}{% endraw %}` | Slice (limit items) |
| `{% raw %}{% if var is mapping %}{% endraw %}` | Type check (dict) |
| `{% raw %}{% if var is string %}{% endraw %}` | Type check (string) |
| `{% raw %}{% if var is defined %}{% endraw %}` | Check if variable exists |
| `{% raw %}{% set x = 'value' %}{% endraw %}` | Variable assignment |
| `{% raw %}{% macro name(arg) %}...{% endmacro %}{% endraw %}` | Reusable macro |
| `{% raw %}{{ loop.last }}{% endraw %}` | True on last loop iteration |
| `{% raw %}{# comment #}{% endraw %}` | Template comment (not in output) |