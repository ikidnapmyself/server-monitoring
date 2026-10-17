import json

import pytest

from apps.notify.templating import NotificationTemplatingService, render_template

INCIDENT = {
    "headline": "Disk almost full on web-1",
    "detail": "/var is 93% full",
    "tone": "critical",
    "action": "Free space on /var",
}
ANALYSIS = {
    "headline": "Logs filled /var",
    "detail": "Rotation stopped",
    "tone": "info",
    "action": "Restart logrotate",
}
JSON_TEMPLATES = ("generic_payload.j2", "pagerduty_payload.j2", "slack_text.j2")
HUMAN_TEMPLATES = ("email_text.j2", "email_html.j2", "incident_notification.j2", "slack_text.j2")


def _ctx(described=True, **over):
    context = (
        {
            "incident": INCIDENT,
            "findings": [INCIDENT],
            "analysis": ANALYSIS,
            "console_url": "https://hub/console/incidents/7/",
            "trace_id": "trace-9",
        }
        if described
        else {}
    )
    message = {
        "title": "[CRITICAL] Disk almost full on web-1",
        "message": "Body line",
        "severity": "critical",
        "channel": "ops",
        "tags": {},
        "context": context,
    }
    message.update(over)
    return NotificationTemplatingService().build_template_context(message)


@pytest.mark.parametrize("name", JSON_TEMPLATES)
@pytest.mark.parametrize("described", [True, False])
def test_json_templates_render_valid_json(name, described):
    json.loads(render_template(f"file:{name}", _ctx(described)))


@pytest.mark.parametrize("name", HUMAN_TEMPLATES)
def test_human_templates_show_title_and_trace(name):
    out = render_template(f"file:{name}", _ctx())

    assert "Disk almost full on web-1" in out
    assert "trace-9" in out


@pytest.mark.parametrize("name", HUMAN_TEMPLATES)
def test_bare_messages_render_without_a_trace(name):
    out = render_template(f"file:{name}", _ctx(described=False))

    assert "Body line" in out
    assert "Trace" not in out


def test_email_html_renders_descriptions():
    out = render_template("file:email_html.j2", _ctx())

    assert "/var is 93% full" in out
    assert "Free space on /var" in out
    assert "Logs filled /var" in out
    assert "Rotation stopped" in out
    assert "Restart logrotate" in out
    assert 'href="https://hub/console/incidents/7/"' in out
    assert "Body line" not in out


def test_email_html_skips_empty_parts():
    blank = {"headline": "h", "detail": "", "tone": "ok", "action": ""}
    ctx = _ctx()
    ctx.update(incident=blank, findings=[], analysis=blank, console_url="")

    out = render_template("file:email_html.j2", ctx)

    assert "Do this" not in out
    assert "Failing checks" not in out
    assert "Open in the console" not in out


def test_email_html_escapes():
    out = render_template("file:email_html.j2", _ctx(title="<script>"))

    assert "<script>" not in out


def test_slack_escapes_control_characters():
    out = render_template("file:slack_text.j2", _ctx(message="a <!channel> & b"))

    body = json.loads(out)["blocks"][1]["text"]["text"]
    assert body == "a &lt;!channel&gt; &amp; b"


def test_slack_truncates_a_long_body():
    out = json.loads(render_template("file:slack_text.j2", _ctx(message="x" * 5000)))

    body = out["blocks"][1]["text"]["text"]
    assert len(body) <= 2900
    assert body.endswith("... (truncated)")


def test_slack_omits_an_empty_body():
    out = json.loads(render_template("file:slack_text.j2", _ctx(described=False, message="")))

    assert len(out["blocks"]) == 1


def test_pagerduty_summary_has_one_prefix():
    out = json.loads(render_template("file:pagerduty_payload.j2", _ctx()))

    assert out["summary"] == "[CRITICAL] Disk almost full on web-1"
    assert out["custom_details"] == {"message": "Body line", "trace_id": "trace-9"}


def test_generic_payload_carries_descriptions():
    out = json.loads(render_template("file:generic_payload.j2", _ctx()))

    assert out["incident"] == INCIDENT
    assert out["analysis"] == ANALYSIS
    assert out["console_url"] == "https://hub/console/incidents/7/"
