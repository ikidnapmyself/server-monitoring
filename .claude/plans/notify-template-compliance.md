# Plan: Make apps/notify Fully Template-Compliant

## Goal

Ensure all notification drivers in `apps/notify` use templates programmatically with **no hardcoded fallbacks, exceptions, or workarounds**. All presentation content must flow through the Jinja2 templating system as specified in `agents.md` and `README.md`.

## Current State Analysis

### Compliance Issues Identified

1. **Email Driver (`email.py`)** - Major violation
   - Lines 42-46: Has `or` fallback to `_build_text_body()` and `_build_html_body()`
   - Lines 59-79: Hardcoded `_build_text_body()` method with static strings
   - Lines 81-112: Hardcoded `_build_html_body()` method with inline HTML/CSS

2. **PagerDuty Driver (`pagerduty.py`)** - Minor violation
   - Line 55: Hardcoded summary format `f"[{message.severity.upper()}] {message.title}"`
   - Template is used but only for `custom_details`, not the main summary field

3. **Generic Driver (`generic.py`)** - Minor violation
   - Lines 55-59: Legacy `_apply_template()` fallback for dict-based templates
   - Lines 62-70: Hardcoded default payload structure fallback

4. **Slack Driver (`slack.py`)** - Compliant
   - Lines 43-44: Minor fallback to `message.message` if no template renders
   - Otherwise properly uses `_render_message_templates()`

### What's Working Correctly

- `templating.py` properly enforces template existence (raises `ValueError` if none found)
- `_render_message_templates()` searches for driver-default templates
- All drivers have corresponding `*_text.j2` or `*_payload.j2` templates
- Template context building is robust with good fallbacks

## Implementation Tasks

### Task 1: Update email_text.j2 template to match fallback content

**File:** `apps/notify/templates/email_text.j2`

Update to include tags and context sections that exist in the hardcoded fallback.

**Changes:**
```jinja2
Alert: {{ title }}
Severity: {{ severity | upper }}

{{ message }}

{% if intelligence and intelligence.summary %}
Summary:
{{ intelligence.summary }}

{% endif %}
{% if recommendations %}
Recommendations:
{% for r in recommendations[:10] %}
- {{ r.title | default('Recommendation') }}: {{ r.description | default('') }}
{% endfor %}

{% endif %}
{% if tags %}
Tags:
{% for key, value in tags.items() %}
  {{ key }}: {{ value }}
{% endfor %}

{% endif %}
{% if context %}
Context:
{% for key, value in context.items() %}
  {{ key }}: {{ value }}
{% endfor %}
{% endif %}

{% if incident.ingest %}
Ingest Summary:
{{ incident.ingest }}

{% endif %}
{% if incident.check %}
Check Summary:
{{ incident.check }}
{% endif %}
```

**Verification:** Compare rendered output with existing `_build_text_body()` output.

---

### Task 2: Update email_html.j2 template to match fallback content

**File:** `apps/notify/templates/email_html.j2`

Update to include:
- Severity-specific border color
- Tags section
- Context section
- Same styling as hardcoded version

**Changes:**
```jinja2
{%- set colors = {'critical': '#dc3545', 'warning': '#ffc107', 'info': '#17a2b8', 'success': '#28a745'} -%}
{%- set color = colors.get(severity, '#6c757d') -%}
<html>
<body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
  <div style="border-left: 4px solid {{ color }}; padding-left: 15px;">
    <h2 style="margin: 0 0 10px 0; color: {{ color }};">{{ title }}</h2>
    <span style="background-color: {{ color }}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 12px;">
      {{ severity | upper }}
    </span>
  </div>
  <p style="margin-top: 20px; line-height: 1.6;">{{ message }}</p>
  {% if intelligence and intelligence.summary %}
  <h3>Summary</h3>
  <p>{{ intelligence.summary }}</p>
  {% endif %}
  {% if recommendations %}
  <div style="margin-top: 20px;">
    <strong>Recommendations:</strong>
    <ul>
    {% for r in recommendations[:20] %}
      <li><strong>{{ r.title | default('Recommendation') }}</strong>: {{ r.description | default('') }}</li>
    {% endfor %}
    </ul>
  </div>
  {% endif %}
  {% if tags %}
  <div style="margin-top: 20px;"><strong>Tags:</strong><ul>
  {% for key, value in tags.items() %}
    <li><code>{{ key }}</code>: {{ value }}</li>
  {% endfor %}
  </ul></div>
  {% endif %}
  {% if context %}
  <div style="margin-top: 20px;"><strong>Context:</strong><ul>
  {% for key, value in context.items() %}
    <li><code>{{ key }}</code>: {{ value }}</li>
  {% endfor %}
  </ul></div>
  {% endif %}
  {% if incident.ingest %}
  <hr>
  <h4>Ingest Summary</h4>
  <pre>{{ incident.ingest }}</pre>
  {% endif %}
  {% if incident.check %}
  <h4>Check Summary</h4>
  <pre>{{ incident.check }}</pre>
  {% endif %}
</body>
</html>
```

**Verification:** Compare rendered output with existing `_build_html_body()` output.

---

### Task 3: Remove hardcoded fallback methods from email.py

**File:** `apps/notify/drivers/email.py`

**Changes:**

1. Remove `_build_text_body()` method (lines 59-79)
2. Remove `_build_html_body()` method (lines 81-112)
3. Update `_build_email()` to require templates (remove `or` fallbacks)

**Updated `_build_email()` (lines 40-46):**
```python
prepared = self._prepare_notification(message, config)

text_body = prepared.get("text")
if not text_body:
    raise ValueError("Email text template required but not rendered")
email.attach(MIMEText(text_body, "plain"))

html_body = prepared.get("html")
if html_body:
    email.attach(MIMEText(html_body, "html"))
```

**Verification:** Run existing tests, ensure emails render from templates only.

---

### Task 4: Update pagerduty_payload.j2 to include summary field

**File:** `apps/notify/templates/pagerduty_payload.j2`

The current template only generates `custom_details`. Update it to provide the full payload structure including the summary field.

**Changes:**
```jinja2
{
  "summary": "[{{ severity | upper }}] {{ title }}",
  "severity": "{{ severity }}",
  "source": "{{ incident.source | default('server-maintenance') }}",
  "component": "{{ tags.component | default(channel) }}",
  "group": "{{ tags.group | default('default') }}",
  "class": "{{ tags.class | default(severity) }}",
  "custom_details": {
    "message": {{ message | tojson }},
    "severity": "{{ severity }}",
    "cpu_count": {{ incident.cpu_count | tojson }},
    "ram_total_human": {{ incident.ram_total_human | tojson }},
    "disk_total_human": {{ incident.disk_total_human | tojson }},
    {% for key, value in tags.items() %}
    "{{ key }}": {{ value | tojson }}{% if not loop.last %},{% endif %}
    {% endfor %}
    {% if context %},
    {% for key, value in context.items() %}
    "{{ key }}": {{ value | tojson }}{% if not loop.last %},{% endif %}
    {% endfor %}
    {% endif %}
    {% if recommendations %},
    "recommendations": [
      {% for r in recommendations %}
      {"title": {{ (r.title | default('')) | tojson }}, "description": {{ (r.description | default('')) | tojson }}}{% if not loop.last %},{% endif %}
      {% endfor %}
    ]
    {% endif %}
  }
}
```

**Verification:** Check that rendered JSON includes all required PagerDuty fields.

---

### Task 5: Update PagerDuty driver to use template for payload structure

**File:** `apps/notify/drivers/pagerduty.py`

**Changes:**

1. Update `_build_payload()` to use rendered template for the `payload` section
2. Remove hardcoded `summary` and field construction
3. Keep routing_key and event_action as they are envelope fields, not content

**Updated `_build_payload()` (simplified):**
```python
def _build_payload(
    self, message: NotificationMessage, config: dict[str, Any]
) -> dict[str, Any]:
    event_action = config.get("event_action", "trigger")

    payload: dict[str, Any] = {
        "routing_key": config["integration_key"],
        "event_action": event_action,
    }

    if config.get("dedup_key"):
        payload["dedup_key"] = config["dedup_key"]
    elif message.tags.get("fingerprint"):
        payload["dedup_key"] = message.tags["fingerprint"]

    if event_action == "trigger":
        prepared = self._prepare_notification(message, config)

        # Template must provide the payload structure
        if prepared.get("payload_obj"):
            payload["payload"] = prepared["payload_obj"]
        else:
            raise ValueError("PagerDuty payload template required but not rendered")

        # Add incident for tracking
        payload["incident"] = prepared.get("incident")

        if config.get("client"):
            payload["client"] = config["client"]
        if config.get("client_url"):
            payload["client_url"] = config["client_url"]

        if message.context.get("url"):
            payload["links"] = [
                {
                    "href": message.context["url"],
                    "text": "View Details",
                }
            ]

    return payload
```

**Verification:** Run PagerDuty tests, verify payload structure matches API requirements.

---

### Task 6: Update generic_payload.j2 template to be comprehensive

**File:** `apps/notify/templates/generic_payload.j2`

Update to include all fields from the default payload structure.

**Changes:**
```jinja2
{
  "title": {{ title | tojson }},
  "message": {{ message | tojson }},
  "severity": "{{ severity }}",
  "channel": "{{ channel }}",
  "tags": {{ tags | tojson }},
  "context": {{ context | tojson }},
  {% if intelligence and intelligence.summary %}
  "summary": {{ intelligence.summary | tojson }},
  {% endif %}
  "incident_id": {{ incident_id | tojson }},
  {% if recommendations %}
  "recommendations": [
    {% for r in recommendations %}
    {"title": {{ (r.title | default('')) | tojson }}, "description": {{ (r.description | default('')) | tojson }}}{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
  {% endif %}
}
```

**Verification:** Compare with existing default payload structure.

---

### Task 7: Remove legacy fallbacks from generic.py

**File:** `apps/notify/drivers/generic.py`

**Changes:**

1. Remove `_apply_template()` method (lines 72-94)
2. Simplify `_build_payload()` to require template rendering

**Updated `_build_payload()`:**
```python
def _build_payload(
    self, message: NotificationMessage, config: dict[str, Any]
) -> dict[str, Any]:
    prepared = self._prepare_notification(message, config)

    # Template must provide payload structure
    if prepared.get("payload_obj"):
        payload = prepared["payload_obj"]
        if isinstance(payload, dict):
            payload.setdefault("incident", prepared.get("incident"))
        return payload

    if prepared.get("payload_raw"):
        return {
            "title": message.title,
            "message": prepared.get("payload_raw"),
            "incident": prepared.get("incident"),
        }

    raise ValueError("Generic driver payload template required but not rendered")
```

**Verification:** Run generic driver tests.

---

### Task 8: Fix Slack driver fallback

**File:** `apps/notify/drivers/slack.py`

**Changes:**

Remove the `message.message` fallback on lines 43-44:
```python
# Before:
if not rendered_text:
    rendered_text = message.message or ""

# After (remove these lines entirely or raise error):
if not rendered_text:
    raise ValueError("Slack template required but not rendered")
```

**Verification:** Run Slack driver tests.

---

### Task 9: Update tests to verify template-only rendering

**Files:** `apps/notify/tests/drivers/test_*.py`

Ensure tests verify:
1. Templates are always used (no fallbacks)
2. Missing templates raise `ValueError`
3. Rendered content matches expected format

**Verification:** Run full test suite.

---

### Task 10: Update agents.md to document strict enforcement

**File:** `apps/notify/agents.md`

Add clarification that templates are strictly required:

```markdown
## Templates are strictly required

As of this version, **all drivers require templates**. There are no hardcoded fallbacks:

- Missing templates will raise `ValueError`
- Configure templates via `NotificationChannel.config` or ensure driver-default files exist
- This ensures consistent, maintainable notification formatting
```

**Verification:** Review documentation matches implementation.

---

## Verification Commands

After each task:
```bash
# Run tests for notify app
uv run pytest apps/notify/tests/ -v

# Run specific driver tests
uv run pytest apps/notify/tests/drivers/ -v

# Test template rendering manually
uv run python -c "
from apps.notify.templating import render_template
ctx = {'title': 'Test', 'severity': 'info', 'message': 'Test msg', 'tags': {}, 'context': {}, 'incident': {}, 'intelligence': None, 'recommendations': []}
print(render_template('file:email_text.j2', ctx))
"
```

## Risk Assessment

- **Low risk:** Template updates (Tasks 1, 2, 4, 6) - additive changes
- **Medium risk:** Driver code changes (Tasks 3, 5, 7, 8) - may break existing behavior
- **Mitigation:** Run tests after each task, compare output before/after

## Notes

- The `_apply_template()` method in generic.py uses simple `{variable}` substitution, not Jinja2. This is a workaround that should be removed.
- Email templates need to handle the case where `ingest` and `check` variables come from `incident` dict, not top-level context.