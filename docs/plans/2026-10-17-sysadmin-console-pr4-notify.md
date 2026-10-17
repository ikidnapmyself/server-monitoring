---
title: "Sysadmin Console PR 4: Notify on Describers"
parent: Plans
---

{% raw %}

# Sysadmin console PR 4: notify on describers

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** A notification says what's wrong, where, how bad and what to do, in the same words
the console uses, with the trace ID in the footer and no raw stage dumps.

**Architecture:** `NotifyExecutor` stops building Markdown stage summaries. A new
`apps/orchestration/notification.py` calls the describers (`describe_incident`,
`describe_findings`, `describe_analysis`) and returns a title, severity, plain-text body and a
JSON-safe context. Notify stays dumb: one template context, built from the message, holds
`incident`, `findings`, `analysis`, `console_url` and `trace_id`. The six templates rebuild on
those keys. The raw `ingest`, `check` and `intelligence` keys, the psutil host metrics and the
recommendation normalizer go.

**Tech Stack:** Django 5.2, Jinja2 sandbox, pytest-django, dataclasses.

Design: `2026-10-15-sysadmin-console-foundation-design.md`, section 4 and section 5 step 4.

## Decisions made for this PR (2026-10-17)

- **Notify renders once.** Today the executor renders the channel's `template` into the
  message body, then the driver renders `config["template"]` again with a different context.
  The executor's render goes. The driver renders every template, from one context.
- **Descriptions travel as plain dicts.** `NotificationMessage.context` is stored in the run's
  snapshot, so it holds JSON. Each `Description` becomes
  `{"headline", "detail", "tone", "action"}`, with `action` as its text. Facts stay out, since
  messages carry no facts but the trace ID. Intents stay out because notify can't resolve them.
- **AI recommendations and actions leave messages.** The design's context holds the analysis
  `Description` only. The console still shows the full advice, one click away via `console_url`.
- **`CONSOLE_URL` is the hub's origin**, such as `https://hub.example.com`. The link appends
  `reverse("console:incident")`. Unset means no link.
- **The title keeps its status prefix.** `[CRITICAL] Disk almost full on web-1`, or
  `[RESOLVED] ...` once settled. Email and PagerDuty stop adding a second `[SEVERITY]` prefix.
- **The generic webhook payload changes shape.** It drops `tags`, `context`, `incident_id`,
  `summary`, `probable_cause` and `recommendations`, and gains `incident`, `findings`,
  `analysis`, `console_url` and `trace_id`. There's no user base to migrate.

## Scope notes

- No model or migration changes.
- `_PAYLOAD_TEMPLATE_KEYS` stays. The channel config still reaches the driver, which still
  renders `config["template"]`.
- Messages sent through `/notify/send/`, `test_notify` and PagerDuty acknowledge/resolve carry
  no descriptions. Every template falls back to `title` and `message` for them.

## Conventions for every task

- Branch `feat/notify-on-describers` off `main`. No worktree.
- TDD: failing test, run it, implement, run it, commit.
- 100% branch coverage on changed code:
  `uv run coverage run -m pytest <paths> && uv run coverage report --include=<files> -m`.
- Commit through pre-commit with a 600000ms timeout. Never `--no-verify`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em or en dashes in code, strings or docs. Comments only for non-obvious constraints.

---

### Task 1: Check findings describer

The console groups check results by status in `apps/console/queries/incident.py`. Notify needs
the same problems, worst first. Move the grouping into the checkers' describer and have the
console reuse it.

**Files:**
- Modify: `apps/checkers/describing.py`
- Modify: `apps/console/queries/incident.py:44,130-170`
- Test: `apps/checkers/_tests/test_describing.py`

**Step 1: Write the failing tests.**

```python
from apps.checkers.checkers import CheckStatus
from apps.checkers.describing import PROBLEM_ORDER, describe_checks, describe_findings


def _snapshot(*checks):
    return {"checks": list(checks)}


def test_findings_are_problems_worst_first():
    snapshot = _snapshot(
        {"name": "cpu", "status": "warning", "metrics": {"cpu_percent": 85}, "message": "x"},
        {"name": "memory", "status": "ok", "metrics": {}, "message": "fine"},
        {"name": "disk", "status": "critical", "metrics": {}, "message": "full"},
    )

    found = describe_findings(snapshot)

    assert [d.tone for d in found] == ["critical", "warning"]


def test_findings_skip_ok_and_skipped():
    snapshot = _snapshot(
        {"name": "cpu", "status": "ok", "metrics": {}, "message": ""},
        {"name": "raid", "status": "skipped", "metrics": {}, "message": "no RAID"},
    )

    assert describe_findings(snapshot) == ()


def test_checks_group_by_status():
    found = describe_checks(_snapshot({"name": "cpu", "status": "ok", "metrics": {}}))

    assert len(found[CheckStatus.OK]) == 1
    assert set(found) == set(CheckStatus)


def test_findings_read_any_shape():
    assert describe_findings(None) == ()
    assert describe_findings({"checks": "nope"}) == ()
    assert describe_findings({"checks": ["nope", {"status": "critical", "metrics": []}]})[0]


def test_problem_order():
    assert PROBLEM_ORDER == (CheckStatus.CRITICAL, CheckStatus.UNKNOWN, CheckStatus.WARNING)
```

Check the tone each checker's `describe` gives a critical and a warning result. If the first
test's tones differ, assert on `CHECK_STATUS_TONES` values instead of literals.

**Step 2: Run them.** `uv run pytest apps/checkers/_tests/test_describing.py -v`. Expected:
ImportError on `describe_checks`.

**Step 3: Implement** in `apps/checkers/describing.py`:

```python
PROBLEM_ORDER = (CheckStatus.CRITICAL, CheckStatus.UNKNOWN, CheckStatus.WARNING)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def describe_checks(snapshot: Any) -> dict[CheckStatus, list[Description]]:
    """Every result a diagnose snapshot stored, described and grouped by status."""
    found: dict[CheckStatus, list[Description]] = {status: [] for status in CheckStatus}
    checks = snapshot.get("checks") if isinstance(snapshot, dict) else None
    for check in checks if isinstance(checks, list) else ():
        if not isinstance(check, dict):
            continue
        status = check_status(check.get("status"))
        metrics = check.get("metrics")
        found[status].append(
            describe_check(
                _text(check.get("name")),
                status,
                metrics if isinstance(metrics, dict) else {},
                _text(check.get("message")),
            )
        )
    return found


def describe_findings(snapshot: Any) -> tuple[Description, ...]:
    """The problems a diagnose snapshot found, worst first."""
    found = describe_checks(snapshot)
    return tuple(d for status in PROBLEM_ORDER for d in found[status])
```

Then in `apps/console/queries/incident.py`, delete the local `PROBLEM_ORDER` and rewrite
`_check_body`:

```python
def _check_body(snapshot: dict[str, Any], raw: str) -> CheckBody:
    found = describe_checks(snapshot)
    return CheckBody(
        problems=tuple(d for status in PROBLEM_ORDER for d in found[status]),
        ok_count=len(found[CheckStatus.OK]),
        skipped_count=len(found[CheckStatus.SKIPPED]),
        errors=describe_check_errors(_texts(snapshot.get("errors"))),
        raw=raw,
    )
```

Import `PROBLEM_ORDER` and `describe_checks` from `apps.checkers.describing`. Drop the imports
of `check_status` and `describe_check` there if nothing else uses them. Keep `_dicts` only if
another caller remains.

**Step 4: Run.** `uv run pytest apps/checkers apps/console -q`. Expected: all pass.

**Step 5: Commit.** `feat(checkers): describe a diagnose snapshot's findings`

---

### Task 2: CONSOLE_URL setting

**Files:**
- Modify: `config/settings.py` (next to `HUB_URL`)
- Modify: `.env.sample`

**Step 1: Add the setting.**

```python
# The origin operators reach this hub's console at, such as https://hub.example.com.
# Notifications link to the incident when set. HUB_URL can't serve: on an agent it names
# another machine.
CONSOLE_URL = os.environ.get("CONSOLE_URL", "")
```

**Step 2:** Add to `.env.sample` under the cluster block:

```
# Hub: the origin your console is served at. Notifications link to the incident page.
# CONSOLE_URL=https://monitoring-hub.example.com
```

**Step 3:** `uv run python manage.py check`. Expected: no issues.

**Step 4: Commit.** `feat(config): CONSOLE_URL for incident links in notifications`

---

### Task 3: The notification builder

**Files:**
- Create: `apps/orchestration/notification.py`
- Test: `apps/orchestration/_tests/test_notification.py`

**Step 1: Write the failing tests.**

```python
import pytest
from django.test import override_settings
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.orchestration.notification import build_notification, console_url

pytestmark = pytest.mark.django_db


def _incident(severity="critical", status="open"):
    incident = Incident.objects.create(
        severity=severity, status=status, subject="checker:disk", instance="web-1"
    )
    Alert.objects.create(
        fingerprint="check:web-1:disk",
        source="cluster",
        name="disk",
        severity=severity,
        started_at=timezone.now(),
        incident=incident,
        labels={"checker": "disk", "instance": "web-1"},
        raw_payload={"metrics": {"percent": 93}},
    )
    return incident


def test_no_incident_is_a_bare_event():
    n = build_notification(None, {}, "t-1")

    assert (n.title, n.severity, n.body) == ("Monitoring event", "info", "")
    assert n.context == {"trace_id": "t-1"}


def test_title_and_severity_come_from_the_incident():
    n = build_notification(_incident(), {}, "t-1")

    assert n.severity == "critical"
    assert n.title.startswith("[CRITICAL] ")
    assert "web-1" in n.title


def test_a_settled_incident_leads_with_its_status():
    n = build_notification(_incident(severity="warning", status="resolved"), {}, "t-1")

    assert n.severity == "warning"
    assert n.title.startswith("[RESOLVED] ")


def test_an_unknown_severity_reads_as_info():
    n = build_notification(_incident(severity="bogus"), {}, "t-1")

    assert n.severity == "info"


def test_context_holds_plain_descriptions():
    previous = {
        "check": {"checks": [{"name": "disk", "status": "critical", "metrics": {}}]},
        "analyze": {"summary": "Logs filled /var", "failure": "", "probable_cause": "rotation"},
    }

    n = build_notification(_incident(), previous, "t-1")

    assert set(n.context) == {"incident", "findings", "analysis", "console_url", "trace_id"}
    assert set(n.context["incident"]) == {"headline", "detail", "tone", "action"}
    assert len(n.context["findings"]) == 1
    assert n.context["analysis"]["headline"] == "Logs filled /var"
    assert "check:web-1:disk" not in str(n.context)


def test_no_analyze_stage_means_no_analysis():
    assert build_notification(_incident(), {}, "t").context["analysis"] is None


def test_body_reads_top_down():
    previous = {
        "check": {"checks": [{"name": "disk", "status": "critical", "metrics": {}}]},
        "analyze": {"failure": "auth", "fallback_used": True},
    }

    body = build_notification(_incident(), previous, "t").body

    assert "Failing checks:" in body
    assert "AI analysis: AI analysis failed: the provider rejected the API key" in body
    assert "Do this: Update the provider's API key" in body


@override_settings(CONSOLE_URL="https://hub.example.com/")
def test_console_url_when_set():
    incident = _incident()

    n = build_notification(incident, {}, "t")

    assert n.context["console_url"] == f"https://hub.example.com/console/incidents/{incident.pk}/"
    assert n.body.endswith(f"Open in the console: {n.context['console_url']}")


@override_settings(CONSOLE_URL="")
def test_no_console_url_when_unset():
    assert console_url(1) == ""
```

Adjust the alert fields to whatever `Alert` requires today (`labels`, `raw_payload`). If the
disk checker's describer needs other metric keys for a critical headline, use the ones its
tests use.

**Step 2: Run.** `uv run pytest apps/orchestration/_tests/test_notification.py -v`. Expected:
ModuleNotFoundError.

**Step 3: Implement** `apps/orchestration/notification.py`:

```python
"""What a notification says about an incident run, built from the apps' describers."""

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.urls import reverse

from apps.alerts.describing import describe_incident
from apps.checkers.describing import describe_findings
from apps.intelligence.describing import describe_analysis
from config.describing import Description

_SEVERITIES = ("critical", "warning", "info")
_SETTLED = ("acknowledged", "resolved", "closed")


@dataclass(frozen=True)
class Notification:
    title: str
    severity: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


def _plain(d: Description) -> dict[str, str]:
    """A description as JSON a template can read. Facts and intents stay behind."""
    return {
        "headline": d.headline,
        "detail": d.detail,
        "tone": d.tone,
        "action": d.action.text if d.action else "",
    }


def console_url(incident_pk: int) -> str:
    base = settings.CONSOLE_URL.rstrip("/")
    return f"{base}{reverse('console:incident', args=[incident_pk])}" if base else ""


def _body(
    incident: dict[str, str],
    findings: list[dict[str, str]],
    analysis: dict[str, str] | None,
    link: str,
) -> str:
    lines = [incident["detail"]] if incident["detail"] else []
    if incident["action"]:
        lines.append(f"Do this: {incident['action']}")
    if findings:
        lines += ["", "Failing checks:", *(f"- {f['headline']}" for f in findings)]
    if analysis:
        lines += ["", f"AI analysis: {analysis['headline']}"]
        if analysis["detail"]:
            lines.append(analysis["detail"])
        if analysis["action"]:
            lines.append(f"Do this: {analysis['action']}")
    if link:
        lines += ["", f"Open in the console: {link}"]
    return "\n".join(lines).strip()


def build_notification(incident: Any, previous: dict[str, Any], trace_id: str) -> Notification:
    if incident is None:
        return Notification("Monitoring event", "info", "", {"trace_id": trace_id})
    severity = incident.severity if incident.severity in _SEVERITIES else "info"
    # A settled incident is announced by its state: an all-clear titled
    # "[CRITICAL] Disk full" reads as a new fire.
    prefix = incident.status if incident.status in _SETTLED else severity
    described = _plain(describe_incident(incident))
    findings = [_plain(d) for d in describe_findings(previous.get("check"))]
    analysis = _plain(describe_analysis(previous["analyze"])) if "analyze" in previous else None
    link = console_url(incident.pk)
    return Notification(
        title=f"[{prefix.upper()}] {described['headline']}",
        severity=severity,
        body=_body(described, findings, analysis, link),
        context={
            "incident": described,
            "findings": findings,
            "analysis": analysis,
            "console_url": link,
            "trace_id": trace_id,
        },
    )
```

**Step 4: Run** the tests and coverage:
`uv run coverage run -m pytest apps/orchestration/_tests/test_notification.py && uv run coverage report --include=apps/orchestration/notification.py -m`.
Expected: pass, 100%.

**Step 5: Commit.** `feat(orchestration): build notifications from the describers`

---

### Task 4: NotifyExecutor sends the built notification

**Files:**
- Modify: `apps/orchestration/executors.py` (`NotifyExecutor`, imports at the top)
- Delete: `apps/orchestration/formatters.py`, `apps/orchestration/_tests/test_formatters.py`
- Modify: `apps/orchestration/_tests/test_executors.py`

**Step 1: Write the failing test** in `TestNotifyExecutorDownstreamHeadline`:

```python
    def test_the_message_carries_descriptions_not_stage_dumps(self):
        incident = self._incident()

        message = self._notify(incident.id, previous={"check": {"checks_run": 1}})

        assert set(message.context) == {
            "incident", "findings", "analysis", "console_url", "trace_id",
        }
        assert message.context["trace_id"] == "trace-abc"
        assert "Check Summary" not in message.message
```

**Step 2: Run.** Expected: FAIL, context has `ingest`, `check`, `intelligence`.

**Step 3: Implement.** In `NotifyExecutor.execute`:

- Replace everything from `# Title and severity are AUTHORITATIVE` through the
  `fallback_used` note with:

  ```python
  incident = self._load_incident(ctx.incident_id)
  notification = build_notification(incident, previous, ctx.trace_id)
  ```

- Delete the `format_*_summary` calls, `template_spec`, `render_ctx` and the
  `render_template` branch. The driver renders the channel's template.
- Build the message from the notification:

  ```python
  message = NotificationMessage(
      title=notification.title,
      message=notification.body,
      severity=notification.severity,
      channel=channel,
      tags={
          "trace_id": ctx.trace_id,
          "run_id": ctx.run_id,
          "idempotency_key": idempotency_key,
      },
      context=notification.context,
  )
  ```

- Delete `_headline_facts`. Keep `_load_incident` and `_route_incident`.
- Import `build_notification` from `apps.orchestration.notification` at module top. Remove
  the `formatters` imports, then delete `formatters.py` and its test file.

**Step 4: Fix the executor tests.** Run `uv run pytest apps/orchestration -q`. Expected
failures and what to do:

- `TestNotifyExecutorTemplateRendering`: the executor no longer renders templates. Delete
  tests that assert on executor-side rendering. Keep any that prove a payload `template` key
  is stripped before `NotifySelector.resolve`, since `_PAYLOAD_TEMPLATE_KEYS` stays.
- `TestNotifyExecutorDownstreamHeadline`: keep the intent. Titles now read
  `[CRITICAL] <problem> on <host>`, so assert the prefix and severity, not the old alert name.
- Tests that built `previous["ingest"]` for a headline with no incident now get
  `Monitoring event` at `info`. Give them a real incident through `incident_id` when the
  headline matters, otherwise drop the headline assertion.

**Step 5: Run** `uv run pytest apps/orchestration -q` and coverage on `executors.py`.
Expected: pass, changed lines covered.

**Step 6: Commit.** `feat(orchestration)!: notify sends describer output, not stage dumps`
with `BREAKING CHANGE: NotificationMessage.context holds incident, findings, analysis,
console_url and trace_id. The ingest, check and intelligence summaries are gone.`

---

### Task 5: One template context in notify

**Files:**
- Modify: `apps/notify/templating.py` (`NotificationTemplatingService`, drop `psutil` and
  `pprint` imports)
- Modify: `apps/notify/drivers/base.py`, `generic.py`, `pagerduty.py`, `email.py`
- Test: `apps/notify/_tests/test_templating.py`, `apps/notify/_tests/drivers/`

**Step 1: Write the failing tests** in `test_templating.py`:

```python
from apps.notify.templating import NotificationTemplatingService


def _message(context=None, tags=None):
    return {
        "title": "[CRITICAL] Disk almost full on web-1",
        "message": "body",
        "severity": "critical",
        "channel": "ops",
        "tags": tags or {},
        "context": context or {},
    }


def test_context_exposes_descriptions():
    incident = {"headline": "h", "detail": "", "tone": "critical", "action": ""}
    ctx = NotificationTemplatingService().build_template_context(
        _message({"incident": incident, "findings": [incident], "console_url": "u",
                  "trace_id": "t"})
    )

    assert ctx["incident"] == incident
    assert ctx["findings"] == [incident]
    assert ctx["analysis"] is None
    assert (ctx["console_url"], ctx["trace_id"]) == ("u", "t")
    assert "recommendations" not in ctx
    assert "context" not in ctx


def test_context_falls_back_for_bare_messages():
    ctx = NotificationTemplatingService().build_template_context(
        _message(tags={"trace_id": "from-tags"})
    )

    assert ctx["incident"] is None
    assert ctx["findings"] == []
    assert ctx["console_url"] == ""
    assert ctx["trace_id"] == "from-tags"
    assert ctx["generated_at"]
```

**Step 2: Run.** Expected: TypeError, `build_template_context` takes two arguments.

**Step 3: Implement.** Replace `compose_incident_details` and `build_template_context` with:

```python
    def build_template_context(self, message_dict: dict[str, Any]) -> dict[str, Any]:
        """Every variable a notification template can read."""
        ctx = message_dict.get("context") or {}
        tags = message_dict.get("tags") or {}
        return {
            "title": message_dict["title"],
            "message": message_dict["message"],
            "severity": message_dict["severity"],
            "channel": message_dict["channel"],
            "tags": tags,
            "trace_id": ctx.get("trace_id") or tags.get("trace_id") or "",
            "incident": ctx.get("incident"),
            "findings": ctx.get("findings") or [],
            "analysis": ctx.get("analysis"),
            "console_url": ctx.get("console_url") or "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
```

In `render_message_templates`, build `ctx = self.build_template_context(message_dict)`.

In `drivers/base.py`: delete `_compose_incident_details`. `_template_context(message)` takes the
message only. `_prepare_notification` returns `text`, `html`, `payload_obj`, `payload_raw`
and no `incident`.

In `drivers/generic.py`: drop `payload.setdefault("incident", ...)` and the `incident` key in
the `payload_raw` wrapper.

In `drivers/pagerduty.py`: drop `payload["incident"]`. Build links from
`message.context.get("console_url")` with the text `"Open in the console"`.

In `drivers/email.py`: `email["Subject"] = message.title`. The title already carries its
prefix.

**Step 4: Run** `uv run pytest apps/notify -q`. Fix tests that assert on removed keys
(`incident`, `cpu_count`, `ram_total_human`, `recommendations_pretty`, the PD `View Details`
link, the doubled email subject). Rewrite each to the new contract. Don't keep tests for
removed behavior.

**Step 5: Coverage** on `templating.py` and the four drivers. Expected: 100% of changed lines.

**Step 6: Commit.** `feat(notify)!: one template context built from descriptions` with
`BREAKING CHANGE: templates lose incident.*, intelligence, recommendations, context, source and
incident_id. They gain incident, findings, analysis, console_url and trace_id.`

---

### Task 6: Rebuild the templates

**Files:**
- Modify: all six files in `apps/notify/templates/`
- Test: `apps/notify/_tests/test_templates.py` (create), `apps/notify/_tests/test_slack_payload.py`

**Step 1: Write the failing tests** in `test_templates.py`:

```python
import json

import pytest

from apps.notify.templating import NotificationTemplatingService, render_template

INCIDENT = {
    "headline": "Disk almost full on web-1",
    "detail": "/var is 93% full",
    "tone": "critical",
    "action": "Free space on /var",
}
ANALYSIS = {"headline": "Logs filled /var", "detail": "Rotation stopped", "tone": "info",
            "action": ""}


def _ctx(described=True, **over):
    context = (
        {"incident": INCIDENT, "findings": [INCIDENT], "analysis": ANALYSIS,
         "console_url": "https://hub/console/incidents/7/", "trace_id": "trace-9"}
        if described else {}
    )
    message = {"title": "[CRITICAL] Disk almost full on web-1", "message": "Body line",
               "severity": "critical", "channel": "ops", "tags": {}, "context": context}
    message.update(over)
    return NotificationTemplatingService().build_template_context(message)


@pytest.mark.parametrize("name", ["generic_payload.j2", "pagerduty_payload.j2", "slack_text.j2"])
@pytest.mark.parametrize("described", [True, False])
def test_json_templates_render_valid_json(name, described):
    json.loads(render_template(f"file:{name}", _ctx(described)))


@pytest.mark.parametrize(
    "name", ["email_text.j2", "email_html.j2", "incident_notification.j2", "slack_text.j2"]
)
def test_human_templates_show_title_and_trace(name):
    out = render_template(f"file:{name}", _ctx())

    assert "Disk almost full on web-1" in out
    assert "trace-9" in out


def test_email_html_renders_descriptions_and_escapes():
    out = render_template("file:email_html.j2", _ctx())

    assert "Free space on /var" in out
    assert "Logs filled /var" in out
    assert 'href="https://hub/console/incidents/7/"' in out
    hostile = render_template("file:email_html.j2", _ctx(title="<script>"))
    assert "<script>" not in hostile


def test_email_html_falls_back_to_the_message():
    assert "Body line" in render_template("file:email_html.j2", _ctx(described=False))


def test_slack_escapes_control_characters():
    out = json.loads(render_template("file:slack_text.j2", _ctx(message="a <!channel> & b")))

    assert "<!channel>" not in json.dumps(out)


def test_pagerduty_summary_has_one_prefix():
    out = json.loads(render_template("file:pagerduty_payload.j2", _ctx()))

    assert out["summary"] == "[CRITICAL] Disk almost full on web-1"
    assert out["custom_details"]["trace_id"] == "trace-9"
```

**Step 2: Run.** Expected: failures on the trace, the double prefix and the HTML sections.

**Step 3: Rewrite the templates.**

`email_text.j2`:

```jinja
{{ title }}
Severity: {{ severity | upper }}

{{ message }}
{% if trace_id %}

Trace {{ trace_id }}
{% endif %}
```

`incident_notification.j2`:

```jinja
# {{ title }}

Severity: **{{ severity | upper }}**

{{ message }}
{% if trace_id %}

---
_Trace {{ trace_id }}_
{% endif %}
```

`email_html.j2`:

```jinja
{%- set colors = {'critical': '#dc3545', 'warning': '#ffc107', 'info': '#17a2b8', 'success': '#28a745'} -%}
{%- set color = colors.get(severity, '#6c757d') -%}
<html>
<body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
  <div style="border-left: 4px solid {{ color }}; padding-left: 15px;">
    <h2 style="margin: 0 0 10px 0; color: {{ color }};">{{ title | e }}</h2>
    <span style="background-color: {{ color }}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 12px;">{{ severity | upper | e }}</span>
  </div>
  {% if incident %}
  {% if incident.detail %}<p style="margin-top: 20px; line-height: 1.6;">{{ incident.detail | e }}</p>{% endif %}
  {% if incident.action %}<p><strong>Do this:</strong> {{ incident.action | e }}</p>{% endif %}
  {% if findings %}
  <h3>Failing checks</h3>
  <ul>{% for f in findings %}<li>{{ f.headline | e }}</li>{% endfor %}</ul>
  {% endif %}
  {% if analysis %}
  <h3>AI analysis</h3>
  <p>{{ analysis.headline | e }}</p>
  {% if analysis.detail %}<p>{{ analysis.detail | e }}</p>{% endif %}
  {% if analysis.action %}<p><strong>Do this:</strong> {{ analysis.action | e }}</p>{% endif %}
  {% endif %}
  {% if console_url %}<p><a href="{{ console_url | e }}">Open in the console</a></p>{% endif %}
  {% else %}
  <p style="margin-top: 20px; line-height: 1.6; white-space: pre-line;">{{ message | e }}</p>
  {% endif %}
  {% if trace_id %}<p style="margin-top: 20px; font-size: 12px; color: #888;">Trace {{ trace_id | e }}</p>{% endif %}
</body>
</html>
```

`generic_payload.j2`:

```jinja
{
  "title": {{ title | tojson }},
  "message": {{ message | tojson }},
  "severity": {{ severity | tojson }},
  "channel": {{ channel | tojson }},
  "incident": {{ incident | tojson }},
  "findings": {{ findings | tojson }},
  "analysis": {{ analysis | tojson }},
  "console_url": {{ console_url | tojson }},
  "trace_id": {{ trace_id | tojson }},
  "generated_at": {{ generated_at | tojson }}
}
```

`pagerduty_payload.j2`:

```jinja
{
  "summary": {{ title | tojson }},
  "severity": {{ severity | tojson }},
  "source": {{ (tags.source or 'server-monitoring') | tojson }},
  "component": {{ (tags.component or channel) | tojson }},
  "group": {{ (tags.group or 'default') | tojson }},
  "class": {{ (tags.class or severity) | tojson }},
  "custom_details": {
    "message": {{ message | tojson }},
    "trace_id": {{ trace_id | tojson }}
  }
}
```

`slack_text.j2`. Slack mrkdwn treats `<`, `>` and `&` as control characters, and a section's
text tops out at 3000:

```jinja
{%- macro clean(s, max_len=2900) -%}
{%- set t = (s or '') | replace('&', '&amp;') | replace('<', '&lt;') | replace('>', '&gt;') | replace('\r', '') | replace('**', '*') -%}
{{- (t[: max_len - 16] ~ '... (truncated)') if t | length > max_len else t -}}
{%- endmacro -%}
{
  "blocks": [
    {"type": "section", "text": {"type": "mrkdwn", "text": {{ ('*' ~ clean(title, 300) ~ '* `' ~ (severity | upper) ~ '`') | tojson }}}}{% if message %},
    {"type": "section", "text": {"type": "mrkdwn", "text": {{ clean(message) | tojson }}}}{% endif %}{% if trace_id %},
    {"type": "context", "elements": [{"type": "mrkdwn", "text": {{ ('Trace ' ~ clean(trace_id, 200)) | tojson }}}]}{% endif %}
  ],
  "text": {{ clean(title) | tojson }}
}
```

Slack links a bare URL in the body, so the console link needs no block of its own.

**Step 4: Run** `uv run pytest apps/notify -q`. Rewrite `test_slack_payload.py` cases that
target recommendations or intelligence to the new blocks. Delete cases for removed inputs.

**Step 5: Commit.** `feat(notify): templates render descriptions with a trace footer`

---

### Task 7: Guard test

No notification shows an identifier outside the trace footer.

**Files:**
- Test: `apps/orchestration/_tests/test_notification.py`

**Step 1: Write the test.** It uses the Task 3 `_incident()` fixture and a check snapshot:

```python
import re

from apps.notify.templating import NotificationTemplatingService, render_template

_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")
_TEMPLATES = (
    "email_text.j2", "email_html.j2", "incident_notification.j2", "slack_text.j2",
    "generic_payload.j2", "pagerduty_payload.j2",
)
_HUMAN = ("email_text.j2", "email_html.j2", "incident_notification.j2")


@pytest.mark.parametrize("name", _TEMPLATES)
def test_no_identifier_reaches_a_notification(name):
    incident = _incident()
    previous = {
        "check": {"checks": [{"name": "disk", "status": "critical", "metrics": {"percent": 93}}]},
        "analyze": {"failure": "timeout", "fallback_used": True, "error_detail": "read timeout"},
    }
    n = build_notification(incident, previous, "trace-1")
    ctx = NotificationTemplatingService().build_template_context(
        {"title": n.title, "message": n.body, "severity": n.severity, "channel": "ops",
         "tags": {}, "context": n.context}
    )

    out = render_template(f"file:{name}", ctx)

    assert "check:web-1:disk" not in out
    assert "checker:disk" not in out
    assert "read timeout" not in out
    if name in _HUMAN:
        assert not _SNAKE.findall(out.replace("trace-1", ""))
```

`email_html.j2` style attributes use hyphens, so the snake check holds. If a checker headline
trips it, the describer is wrong. Fix the describer, not the regex.

**Step 2: Run.** Expected: pass. A failure names the template and the leak.

**Step 3: Commit.** `test(notify): no identifier reaches a notification`

---

### Task 8: Docs

**Files:**
- Modify: `apps/notify/AGENTS.md` ("Content sourcing", the template context list, the
  "Base driver helpers" list and the `slack_text.j2` example)
- Modify: `apps/alerts/AGENTS.md:250` (`derive_headline` becomes `build_notification`)
- Modify: `docs/Templates.md` ("Available Templates" and "Context Variables")
- Modify: `docs/Deployment.md` (add `CONSOLE_URL` beside `HUB_URL` in the hub's env block)

**Content to write:**

- Content sourcing: `NotifyExecutor` calls
  `apps.orchestration.notification.build_notification(incident, previous, trace_id)`. Title
  and severity come from the incident: `[<STATUS or SEVERITY>] <describe_incident headline>`.
  The body and context come from `describe_incident`, `describe_findings` and
  `describe_analysis`. Intelligence never sets the title or severity. A failed analysis says
  so in the body.
- Template context table: `title`, `message`, `severity`, `channel`, `tags`, `trace_id`,
  `incident`, `findings`, `analysis`, `console_url`, `generated_at`. Each description is
  `{headline, detail, tone, action}`. `incident` and `analysis` are null and `findings` empty
  on messages that didn't come from a pipeline run.
- Driver helpers: `_template_context(message)`, `_render_message_templates`,
  `_prepare_notification` returning `text`, `html`, `payload_obj`, `payload_raw`.
- `CONSOLE_URL`: the origin the hub's console is served at. Notifications link to
  `/console/incidents/<pk>/` when it's set.

**Step 1:** Make the edits. Use `grep -rn "derive_headline\|recommendations_pretty\|incident.ingest\|_compose_incident_details" apps docs/*.md config`
to confirm nothing outside `docs/plans/` still names the removed pieces.

**Step 2: Commit.** `docs: notifications render describers; CONSOLE_URL`

---

### Task 9: Verify

**Step 1:** `uv run pytest -q`. Expected: all pass.

**Step 2:** `uv run black . --check && uv run ruff check . && uv run bandit -r apps/ config/ -c pyproject.toml -q`.
Expected: clean.

**Step 3:** `uv run coverage run -m pytest && uv run coverage report --include='apps/orchestration/notification.py,apps/orchestration/executors.py,apps/notify/templating.py,apps/notify/drivers/*.py,apps/checkers/describing.py,apps/console/queries/incident.py' -m`.
Expected: no missed changed lines.

**Step 4:** `uv run python manage.py run_pipeline --sample --dry-run`. Expected: a run that
reaches notify without errors.

**Step 5:** Push and open a PR against `main`. Never merge locally.

## Acceptance

- A notification for an incident run reads `[SEVERITY] <problem> on <host>`, then the detail,
  `Do this:`, failing checks, the AI analysis outcome and the console link.
- No notification template reads `ingest`, `check`, `intelligence`, `recommendations` or
  `context`, and no message carries a fingerprint, subject key or raw provider error.
- The trace ID is the only identifier, in the footer.
- `CONSOLE_URL` unset sends no link and breaks nothing.

{% endraw %}
