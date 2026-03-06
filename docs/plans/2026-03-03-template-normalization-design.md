---
title: "Notification Template Normalization — Design"
parent: Plans
---
# Notification Template Normalization — Design

## Goal

Make all 6 notification templates (email_html, email_text, slack_text, generic_payload, pagerduty_payload, incident_notification) consistent, safe, and complete. Same data shown in format-appropriate ways across every driver.

## Current Problems

### Inconsistencies

- **`incident.ingest` vs `ingest`**: email templates use `incident.ingest`/`incident.check`; `incident_notification.j2` uses bare `ingest`/`check` (different variables).
- **Recommendation caps vary**: email_html=20, email_text=10, slack=5, others=unlimited.
- **Intelligence access differs**: only `incident_notification.j2` uses `probable_cause` and `actions`; Slack has elaborate fallback logic; others only use `summary`.
- **Title formatting inconsistent**: Markdown H1, HTML H2, plain text prefix, Slack bold — all different patterns.

### Missing Data

- No `incident_id` in email/slack/PagerDuty templates.
- No `source` in email templates.
- No `incident.generated_at` (timestamp) in any template.
- `intelligence.actions` only in `incident_notification.j2`.
- `incident.environment` never displayed.

### Robustness Issues

- **XSS in email_html.j2**: `{{ message }}`, `{{ intelligence.summary }}` injected raw into HTML.
- **PagerDuty null metrics**: `cpu_count`, `ram_total_human`, `disk_total_human` can be None, producing JSON `null`.
- **Hand-built JSON**: generic/PD templates construct JSON via string interpolation with conditional commas — trailing comma bugs possible.
- **`incident_notification.j2` deeply nested access**: `r.details.top_processes[:5]` could error if `r.details` is missing.

### Output Quality

- `incident_notification.j2` is effectively orphaned — no driver uses it by default.
- Email HTML has no responsive design (minor, out of scope).
- PagerDuty fallback source string is `server-maintenance` (not project name).

## Shared Context Contract

All templates will use these variables consistently:

| Variable | Type | Description |
|----------|------|-------------|
| `title` | str | Alert title |
| `message` | str | Body text |
| `severity` | str | `critical`, `warning`, `info`, `success` |
| `channel` | str | Routing destination |
| `incident_id` | int/None | Incident identifier |
| `source` | str/None | Alert source system |
| `intelligence.summary` | str/None | AI analysis summary |
| `intelligence.probable_cause` | str/None | Root cause analysis |
| `intelligence.actions` | list/None | Recommended actions |
| `recommendations` | list[dict]/None | `title`, `description`, `priority` per item |
| `tags` | dict | Custom tags |
| `context` | dict | Custom context key-value pairs |
| `incident.ingest` | any/None | Raw ingest stage output |
| `incident.check` | any/None | Raw check stage output |
| `incident.generated_at` | str/None | ISO8601 timestamp |
| `incident.environment` | str/None | Environment name |
| `incident.cpu_count` | int/None | System CPU count |
| `incident.ram_total_human` | str/None | Human-readable RAM |
| `incident.disk_total_human` | str/None | Human-readable disk |

**Rule:** Stage summaries accessed via `incident.ingest`/`incident.check` everywhere.

## Changes Per Template

### `email_html.j2`

- Add `|e` escape filter to all interpolated values (XSS fix).
- Add `incident_id`, `source`, `incident.generated_at` to header.
- Add `intelligence.probable_cause` and `intelligence.actions` sections.
- Cap recommendations at 10 (down from 20).

### `email_text.j2`

- Add `incident_id`, `source`, `incident.generated_at` to header.
- Add `intelligence.probable_cause` and `intelligence.actions` sections.
- Already uses `incident.ingest`/`incident.check` — no change needed there.

### `slack_text.j2`

- Add `incident_id` to header block (after severity).
- Add `source` to header if present.
- Keep existing intelligence fallback logic (most robust).
- Keep recommendation cap at 5 (Slack payload limits).
- Keep sanitizer as-is.

### `generic_payload.j2`

- Add `source`, `incident.generated_at` fields.
- Add `intelligence.probable_cause` if present.
- Guard empty `tags`/`context` trailing commas — use `tojson` more aggressively.
- Cap recommendations at 10.

### `pagerduty_payload.j2`

- Guard `cpu_count`, `ram_total_human`, `disk_total_human` with conditionals.
- Add `incident.generated_at` to custom_details.
- Guard empty `tags`/`context` trailing comma issue.
- Cap recommendations at 10.

### `incident_notification.j2`

- Change `ingest`/`check` to `incident.ingest`/`incident.check`.
- Remove deeply nested `r.details.large_items` and `r.details.top_processes` — simplify to `r.title`, `r.description`, `r.priority` like others.
- Add `incident.generated_at`.
- Cap recommendations at 10.
- Clarify role: generic Markdown template for custom/override use.

## Out of Scope

- No shared Jinja2 macros/includes (over-engineering for 6 small templates).
- No email responsive design overhaul.
- No changes to `templating.py` or driver code — template files only.

## Testing

- Existing template rendering tests continue to pass.
- Add/update tests for `|e` escaping in email HTML.
- Verify PagerDuty produces valid JSON when metrics are None.
- Verify no trailing comma issues in generic/PD templates with empty tags/context.