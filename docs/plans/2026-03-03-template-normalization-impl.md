---
title: "Notification Template Normalization — Implementation Plan"
parent: Plans
nav_exclude: true
---
{% raw %}
# Notification Template Normalization — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all 6 notification templates consistent, safe, and complete — same data shown in format-appropriate ways across every driver.

**Architecture:** Template-only changes. Each of the 6 Jinja2 templates under `apps/notify/templates/` gets updated to follow a shared context contract: consistent variable access (`incident.ingest` not bare `ingest`), consistent recommendation caps, `|e` escaping for HTML, null guards for JSON, and missing fields added (`incident_id`, `source`, `generated_at`). No changes to `templating.py`, driver code, or models.

**Tech Stack:** Jinja2 templates (.j2), Django test framework, Python json module for validation.

**Important context:**
- Jinja2 autoescape is **disabled** (`autoescape=False` in `apps/notify/templating.py:39`). HTML escaping must be done manually with `|e` filter.
- Templates are rendered via `render_template(spec, context)` from `apps/notify/templating.py`.
- File templates are loaded with `render_template("file:template_name.j2", context_dict)`.
- The context dict structure is built by `NotificationTemplatingService.build_template_context()` in `apps/notify/templating.py:274-299`.
- Existing tests are in `apps/notify/_tests/test_templating.py` (minimal — 3 tests).
- Template test fixtures use this minimal context pattern:
  ```python
  {"title": "X", "message": "M", "severity": "info", "channel": "c", "tags": {}, "context": {}, "incident": {}}
  ```

---

### Task 1: Fix `email_html.j2` — XSS escaping, missing fields, intelligence sections

**Files:**
- Modify: `apps/notify/templates/email_html.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for email_html.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
class EmailHtmlTemplateTests(SimpleTestCase):
    """Tests for email_html.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops@example.com",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "ingest": "raw ingest data",
                "check": "raw check data",
                "generated_at": "2026-03-03T12:00:00+00:00",
                "environment": "production",
                "cpu_count": 4,
                "ram_total_human": "16.0 GB",
                "disk_total_human": "500.0 GB",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker", "Increase memory limit"],
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker process", "priority": "high"},
                {"title": "Scale up", "description": "Add more RAM", "priority": "medium"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:email_html.j2", ctx)

    def test_html_escapes_title(self):
        out = self._render(title="<script>alert('xss')</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_html_escapes_message(self):
        out = self._render(message="<img src=x onerror=alert(1)>")
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;img", out)

    def test_html_escapes_intelligence_summary(self):
        out = self._render(intelligence={"summary": "<b>bold</b>", "probable_cause": None, "actions": None})
        self.assertNotIn("<b>bold</b>", out)
        self.assertIn("&lt;b&gt;", out)

    def test_includes_incident_id(self):
        out = self._render()
        self.assertIn("42", out)

    def test_includes_source(self):
        out = self._render()
        self.assertIn("local-monitor", out)

    def test_includes_generated_at(self):
        out = self._render()
        self.assertIn("2026-03-03T12:00:00", out)

    def test_includes_probable_cause(self):
        out = self._render()
        self.assertIn("Memory leak in worker", out)

    def test_includes_intelligence_actions(self):
        out = self._render()
        self.assertIn("Restart worker", out)
        self.assertIn("Increase memory limit", out)

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        self.assertIn("Rec 9", out)
        self.assertNotIn("Rec 10", out)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)
        self.assertIn("CPU usage at 95%", out)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::EmailHtmlTemplateTests -v`
Expected: Multiple FAIL — XSS tests fail (no escaping), missing field tests fail (incident_id, source, generated_at, probable_cause, actions not in output), cap test fails (current cap is 20).

**Step 3: Update email_html.j2**

Replace the contents of `apps/notify/templates/email_html.j2` with:

```jinja2
{%- set colors = {'critical': '#dc3545', 'warning': '#ffc107', 'info': '#17a2b8', 'success': '#28a745'} -%}
{%- set color = colors.get(severity, '#6c757d') -%}
<html>
<body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
  <div style="border-left: 4px solid {{ color }}; padding-left: 15px;">
    <h2 style="margin: 0 0 10px 0; color: {{ color }};">{{ title | e }}</h2>
    <span style="background-color: {{ color }}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 12px;">
      {{ severity | upper }}
    </span>
    {% if incident_id %}<span style="margin-left: 8px; font-size: 12px; color: #666;">Incident #{{ incident_id }}</span>{% endif %}
  </div>
  {% if source or incident.generated_at %}
  <p style="margin-top: 10px; font-size: 12px; color: #888;">
    {% if source %}Source: {{ source | e }}{% endif %}
    {% if source and incident.generated_at %} | {% endif %}
    {% if incident.generated_at %}{{ incident.generated_at }}{% endif %}
  </p>
  {% endif %}
  <p style="margin-top: 20px; line-height: 1.6;">{{ message | e }}</p>
  {% if intelligence and intelligence.summary %}
  <h3>Summary</h3>
  <p>{{ intelligence.summary | e }}</p>
  {% endif %}
  {% if intelligence and intelligence.probable_cause %}
  <h3>Probable Cause</h3>
  <p>{{ intelligence.probable_cause | e }}</p>
  {% endif %}
  {% if intelligence and intelligence.actions %}
  <h3>Recommended Actions</h3>
  <ul>
  {% for action in intelligence.actions %}
    <li>{{ action | e }}</li>
  {% endfor %}
  </ul>
  {% endif %}
  {% if recommendations %}
  <div style="margin-top: 20px;">
    <strong>Recommendations:</strong>
    <ul>
    {% for r in recommendations[:10] %}
      <li><strong>{{ r.title | default('Recommendation') | e }}</strong>: {{ r.description | default('') | e }}</li>
    {% endfor %}
    </ul>
  </div>
  {% endif %}
  {% if tags %}
  <div style="margin-top: 20px;"><strong>Tags:</strong><ul>
  {% for key, value in tags.items() %}
    <li><code>{{ key | e }}</code>: {{ value | e }}</li>
  {% endfor %}
  </ul></div>
  {% endif %}
  {% if context %}
  <div style="margin-top: 20px;"><strong>Context:</strong><ul>
  {% for key, value in context.items() %}
    <li><code>{{ key | e }}</code>: {{ value | e }}</li>
  {% endfor %}
  </ul></div>
  {% endif %}
  {% if incident.ingest %}
  <hr>
  <h4>Ingest Summary</h4>
  <pre>{{ incident.ingest | e }}</pre>
  {% endif %}
  {% if incident.check %}
  <h4>Check Summary</h4>
  <pre>{{ incident.check | e }}</pre>
  {% endif %}
</body>
</html>
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::EmailHtmlTemplateTests -v`
Expected: All PASS

**Step 5: Run full test suite to check for regressions**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/email_html.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: email_html.j2 — XSS escaping, missing fields, intelligence sections, cap recs at 10"
```

---

### Task 2: Fix `email_text.j2` — missing fields, intelligence sections

**Files:**
- Modify: `apps/notify/templates/email_text.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for email_text.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
class EmailTextTemplateTests(SimpleTestCase):
    """Tests for email_text.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops@example.com",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "ingest": "raw ingest data",
                "check": "raw check data",
                "generated_at": "2026-03-03T12:00:00+00:00",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker", "Increase memory limit"],
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker process"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:email_text.j2", ctx)

    def test_includes_incident_id(self):
        out = self._render()
        self.assertIn("42", out)

    def test_includes_source(self):
        out = self._render()
        self.assertIn("local-monitor", out)

    def test_includes_generated_at(self):
        out = self._render()
        self.assertIn("2026-03-03T12:00:00", out)

    def test_includes_probable_cause(self):
        out = self._render()
        self.assertIn("Memory leak in worker", out)

    def test_includes_intelligence_actions(self):
        out = self._render()
        self.assertIn("Restart worker", out)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::EmailTextTemplateTests -v`
Expected: FAIL — incident_id, source, generated_at, probable_cause, actions not in output.

**Step 3: Update email_text.j2**

Replace the contents of `apps/notify/templates/email_text.j2` with:

```jinja2
Alert: {{ title }}
Severity: {{ severity | upper }}
{% if incident_id %}Incident ID: {{ incident_id }}
{% endif %}{% if source %}Source: {{ source }}
{% endif %}{% if incident.generated_at %}Time: {{ incident.generated_at }}
{% endif %}
{{ message }}

{% if intelligence and intelligence.summary %}
Summary:
{{ intelligence.summary }}

{% endif %}
{% if intelligence and intelligence.probable_cause %}
Probable Cause:
{{ intelligence.probable_cause }}

{% endif %}
{% if intelligence and intelligence.actions %}
Recommended Actions:
{% for action in intelligence.actions %}- {{ action }}
{% endfor %}

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

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::EmailTextTemplateTests -v`
Expected: All PASS

**Step 5: Run full notify test suite**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/email_text.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: email_text.j2 — add incident_id, source, generated_at, probable_cause, actions"
```

---

### Task 3: Fix `slack_text.j2` — add incident_id and source to header

**Files:**
- Modify: `apps/notify/templates/slack_text.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for slack_text.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
import json


class SlackTextTemplateTests(SimpleTestCase):
    """Tests for slack_text.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "#ops",
            "tags": {},
            "context": {},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {},
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": None,
                "actions": None,
            },
            "recommendations": None,
        }
        ctx.update(overrides)
        return render_template("file:slack_text.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("blocks", data)
        self.assertIn("text", data)

    def test_includes_incident_id_in_header(self):
        out = self._render()
        data = json.loads(out)
        header_text = data["blocks"][0]["text"]["text"]
        self.assertIn("42", header_text)

    def test_includes_source_in_header(self):
        out = self._render()
        data = json.loads(out)
        header_text = data["blocks"][0]["text"]["text"]
        self.assertIn("local-monitor", header_text)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
        )
        data = json.loads(out)
        self.assertIn("blocks", data)

    def test_recommendations_capped_at_5(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(8)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        full_text = json.dumps(data)
        self.assertIn("Rec 4", full_text)
        self.assertNotIn("Rec 5", full_text)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::SlackTextTemplateTests -v`
Expected: FAIL — incident_id and source not in header block.

**Step 3: Update slack_text.j2**

The only change is to the header block line (line 57). Change the header section variables and the first block:

In `apps/notify/templates/slack_text.j2`, replace line 3-4 and 57:

Current (line 3-4):
```jinja2
{% set _title = title or '' %}
{% set _sev = (severity or '') | upper %}
```

Replace with:
```jinja2
{% set _title = title or '' %}
{% set _sev = (severity or '') | upper %}
{% set _id = incident_id or '' %}
{% set _src = source or '' %}
```

Current (line 57):
```jinja2
    {"type": "section", "text": {"type": "mrkdwn", "text": {{ ('*' ~ _title ~ '* — `' ~ _sev ~ '`') | tojson }}}},
```

Replace with:
```jinja2
    {"type": "section", "text": {"type": "mrkdwn", "text": {{ ('*' ~ _title ~ '* — `' ~ _sev ~ '`' ~ (' | #' ~ _id|string if _id else '') ~ (' | ' ~ _src if _src else '')) | tojson }}}},
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::SlackTextTemplateTests -v`
Expected: All PASS

**Step 5: Run full notify test suite**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/slack_text.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: slack_text.j2 — add incident_id and source to header block"
```

---

### Task 4: Fix `generic_payload.j2` — add fields, fix trailing commas, cap recs

**Files:**
- Modify: `apps/notify/templates/generic_payload.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for generic_payload.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
class GenericPayloadTemplateTests(SimpleTestCase):
    """Tests for generic_payload.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "webhook-1",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "generated_at": "2026-03-03T12:00:00+00:00",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker process"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:generic_payload.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["title"], "CPU Alert")

    def test_includes_source(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["source"], "local-monitor")

    def test_includes_generated_at(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("generated_at", data)

    def test_includes_probable_cause(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["probable_cause"], "Memory leak in worker")

    def test_valid_json_with_empty_tags_and_context(self):
        out = self._render(tags={}, context={})
        data = json.loads(out)
        self.assertEqual(data["tags"], {})
        self.assertEqual(data["context"], {})

    def test_valid_json_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        data = json.loads(out)
        self.assertEqual(data["title"], "CPU Alert")

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        self.assertEqual(len(data["recommendations"]), 10)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::GenericPayloadTemplateTests -v`
Expected: FAIL — source, generated_at, probable_cause not in JSON output. Trailing comma may cause JSON parse errors with empty tags/context.

**Step 3: Update generic_payload.j2**

Replace the contents of `apps/notify/templates/generic_payload.j2` with:

```jinja2
{%- set has_recs = recommendations and recommendations[0] is mapping and recommendations[0].title is defined -%}
{
  "title": {{ title | tojson }},
  "message": {{ message | tojson }},
  "severity": "{{ severity }}",
  "channel": {{ channel | tojson }},
  "source": {{ (source or '') | tojson }},
  "incident_id": {{ incident_id | tojson }},
  "generated_at": {{ (incident.generated_at or '') | tojson }},
  "tags": {{ tags | tojson }},
  "context": {{ context | tojson }}{% if intelligence and intelligence.summary %},
  "summary": {{ intelligence.summary | tojson }}{% endif %}{% if intelligence and intelligence.probable_cause %},
  "probable_cause": {{ intelligence.probable_cause | tojson }}{% endif %}{% if has_recs %},
  "recommendations": [
    {% for r in recommendations[:10] %}{"title": {{ (r.title | default('')) | tojson }}, "description": {{ (r.description | default('')) | tojson }}}{% if not loop.last %},
    {% endif %}{% endfor %}
  ]{% endif %}
}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::GenericPayloadTemplateTests -v`
Expected: All PASS

**Step 5: Run full notify test suite**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/generic_payload.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: generic_payload.j2 — add source, generated_at, probable_cause; fix trailing commas; cap recs at 10"
```

---

### Task 5: Fix `pagerduty_payload.j2` — guard null metrics, add fields, fix trailing commas

**Files:**
- Modify: `apps/notify/templates/pagerduty_payload.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for pagerduty_payload.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
class PagerDutyPayloadTemplateTests(SimpleTestCase):
    """Tests for pagerduty_payload.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops-pd",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "source": "local-monitor",
                "generated_at": "2026-03-03T12:00:00+00:00",
                "cpu_count": 4,
                "ram_total_human": "16.0 GB",
                "disk_total_human": "500.0 GB",
            },
            "intelligence": None,
            "recommendations": None,
        }
        ctx.update(overrides)
        return render_template("file:pagerduty_payload.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("summary", data)
        self.assertIn("custom_details", data)

    def test_valid_json_with_null_metrics(self):
        out = self._render(incident={
            "source": None,
            "generated_at": None,
            "cpu_count": None,
            "ram_total_human": None,
            "disk_total_human": None,
        })
        data = json.loads(out)
        # Should not have null metric keys in custom_details
        cd = data["custom_details"]
        self.assertNotIn("cpu_count", cd)
        self.assertNotIn("ram_total_human", cd)
        self.assertNotIn("disk_total_human", cd)

    def test_includes_generated_at(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["custom_details"]["generated_at"], "2026-03-03T12:00:00+00:00")

    def test_valid_json_with_empty_tags_and_context(self):
        out = self._render(tags={}, context={})
        data = json.loads(out)
        self.assertIn("custom_details", data)

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        self.assertEqual(len(data["custom_details"]["recommendations"]), 10)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::PagerDutyPayloadTemplateTests -v`
Expected: FAIL — null metrics produce JSON `null` values, generated_at missing, trailing comma possible with empty tags/context.

**Step 3: Update pagerduty_payload.j2**

Replace the contents of `apps/notify/templates/pagerduty_payload.j2` with:

```jinja2
{%- set has_recs = recommendations and recommendations[0] is mapping and recommendations[0].title is defined -%}
{%- set _src = (incident.source or tags.source or 'server-monitoring') if incident is mapping else (tags.source or 'server-monitoring') -%}
{
  "summary": {{ ('[' ~ (severity | upper) ~ '] ' ~ title) | tojson }},
  "severity": "{{ severity }}",
  "source": {{ _src | tojson }},
  "component": {{ (tags.component or channel) | tojson }},
  "group": {{ (tags.group or 'default') | tojson }},
  "class": {{ (tags.class or severity) | tojson }},
  "custom_details": {
    "message": {{ message | tojson }},
    "severity": "{{ severity }}"{% if incident.generated_at %},
    "generated_at": {{ incident.generated_at | tojson }}{% endif %}{% if incident.cpu_count is not none %},
    "cpu_count": {{ incident.cpu_count | tojson }}{% endif %}{% if incident.ram_total_human %},
    "ram_total_human": {{ incident.ram_total_human | tojson }}{% endif %}{% if incident.disk_total_human %},
    "disk_total_human": {{ incident.disk_total_human | tojson }}{% endif %}{% if tags %},
    {% for key, value in tags.items() %}"{{ key }}": {{ value | tojson }}{% if not loop.last %},
    {% endif %}{% endfor %}{% endif %}{% if context %},
    {% for key, value in context.items() %}"{{ key }}": {{ value | tojson }}{% if not loop.last %},
    {% endif %}{% endfor %}{% endif %}{% if has_recs %},
    "recommendations": [
      {% for r in recommendations[:10] %}{"title": {{ (r.title | default('')) | tojson }}, "description": {{ (r.description | default('')) | tojson }}}{% if not loop.last %},
      {% endif %}{% endfor %}
    ]{% endif %}
  }
}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::PagerDutyPayloadTemplateTests -v`
Expected: All PASS

**Step 5: Run full notify test suite**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/pagerduty_payload.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: pagerduty_payload.j2 — guard null metrics, add generated_at, fix trailing commas, cap recs at 10"
```

---

### Task 6: Fix `incident_notification.j2` — standardize variable access, simplify recommendations

**Files:**
- Modify: `apps/notify/templates/incident_notification.j2`
- Test: `apps/notify/_tests/test_templating.py`

**Step 1: Write failing tests for incident_notification.j2**

Add to `apps/notify/_tests/test_templating.py`:

```python
class IncidentNotificationTemplateTests(SimpleTestCase):
    """Tests for incident_notification.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops",
            "tags": {},
            "context": {},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "ingest": "raw ingest data",
                "check": "raw check data",
                "generated_at": "2026-03-03T12:00:00+00:00",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker"],
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker", "priority": "high"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:incident_notification.j2", ctx)

    def test_uses_incident_ingest_not_bare(self):
        """Verify template uses incident.ingest, not bare ingest variable."""
        out = self._render()
        self.assertIn("raw ingest data", out)

    def test_uses_incident_check_not_bare(self):
        """Verify template uses incident.check, not bare check variable."""
        out = self._render()
        self.assertIn("raw check data", out)

    def test_includes_generated_at(self):
        out = self._render()
        self.assertIn("2026-03-03T12:00:00", out)

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}", "priority": "low"} for i in range(15)]
        out = self._render(recommendations=recs)
        self.assertIn("Rec 9", out)
        self.assertNotIn("Rec 10", out)

    def test_no_deep_nested_details_access(self):
        """Verify simplified recommendations without r.details.large_items etc."""
        recs = [{"title": "Clean disk", "description": "Remove old files", "priority": "high"}]
        out = self._render(recommendations=recs)
        self.assertIn("Clean disk", out)
        self.assertIn("Remove old files", out)
        self.assertNotIn("Large items", out)
        self.assertNotIn("Top processes", out)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_templating.py::IncidentNotificationTemplateTests -v`
Expected: FAIL — template uses bare `ingest`/`check` (not `incident.ingest`/`incident.check`), no generated_at, deep nested details still present.

**Step 3: Update incident_notification.j2**

Replace the contents of `apps/notify/templates/incident_notification.j2` with:

```jinja2
{{ "# " + title }}

Severity: **{{ severity | upper }}**

{% if incident_id %}Incident ID: `{{ incident_id }}`

{% endif %}
{% if source %}Source: `{{ source }}`

{% endif %}
{% if incident.generated_at %}Time: {{ incident.generated_at }}

{% endif %}
{% if intelligence and intelligence.summary %}Summary:
{{ intelligence.summary }}

{% endif %}
{% if intelligence and intelligence.probable_cause %}Probable cause:
{{ intelligence.probable_cause }}

{% endif %}
{% if intelligence and intelligence.actions %}Actions:
{% for a in intelligence.actions %}- {{ a }}
{% endfor %}

{% endif %}
{% if recommendations %}Recommendations (top {{ recommendations[:10]|length }}):
{% for r in recommendations[:10] %}- **{{ r.title | default('') }}** ({{ r.priority | default('') }})
  > {{ r.description | default('') }}

{% endfor %}
{% endif %}

---

_Ingest Summary:_
{% if incident.ingest %}
{{ incident.ingest }}
{% else %}N/A
{% endif %}

_Check Summary:_
{% if incident.check %}
{{ incident.check }}
{% else %}N/A
{% endif %}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_templating.py::IncidentNotificationTemplateTests -v`
Expected: All PASS

**Step 5: Run full notify test suite**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add apps/notify/templates/incident_notification.j2 apps/notify/_tests/test_templating.py
git commit -m "fix: incident_notification.j2 — use incident.ingest/check, simplify recs, add generated_at, cap at 10"
```

---

### Task 7: Final verification — full test suite, linting, formatting

**Files:**
- None modified (verification only)

**Step 1: Run full project test suite**

Run: `uv run pytest`
Expected: All tests PASS

**Step 2: Run black formatter**

Run: `uv run black apps/notify/_tests/test_templating.py`
Expected: Clean or reformatted

**Step 3: Run ruff linter**

Run: `uv run ruff check apps/notify/ --fix`
Expected: Clean

**Step 4: Verify all 6 templates render with minimal context (no crashes)**

Run: `uv run pytest apps/notify/_tests/test_templating.py -v`
Expected: All PASS — including the `test_renders_without_optional_fields` tests for each template that verify graceful handling of None/missing data.

**Step 5: If any fixes needed, commit**

```bash
git add -A
git commit -m "chore: lint and format fixes for template normalization"
```
{% endraw %}