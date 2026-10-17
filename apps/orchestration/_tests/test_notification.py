import re

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.notify.templating import NotificationTemplatingService, render_template
from apps.orchestration.notification import build_notification, console_url

pytestmark = pytest.mark.django_db

CPU_CHECK = {"name": "cpu", "status": "critical", "metrics": {"cpu_percent": 97.0}}


def _incident(severity="critical", status="open"):
    incident = Incident.objects.create(
        severity=severity, status=status, subject="checker:cpu", instance="web-1"
    )
    Alert.objects.create(
        fingerprint="check:web-1:cpu",
        source="cluster",
        name="cpu",
        severity="critical",
        started_at=timezone.now(),
        incident=incident,
        labels={"checker": "cpu", "instance": "web-1"},
        raw_payload={"metrics": {"cpu_percent": 97.0}},
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
    assert n.title.endswith(" on web-1")


def test_a_settled_incident_leads_with_its_status():
    n = build_notification(_incident(severity="warning", status="resolved"), {}, "t-1")

    assert n.severity == "warning"
    assert n.title.startswith("[RESOLVED] ")


def test_an_unknown_severity_reads_as_info():
    assert build_notification(_incident(severity="bogus"), {}, "t-1").severity == "info"


def test_context_holds_plain_descriptions():
    previous = {
        "check": {"checks": [CPU_CHECK]},
        "analyze": {
            "summary": "A backup job pinned the CPU",
            "failure": "",
            "probable_cause": "cron",
        },
    }

    n = build_notification(_incident(), previous, "t-1")

    assert set(n.context) == {"incident", "findings", "analysis", "console_url", "trace_id"}
    assert set(n.context["incident"]) == {"headline", "detail", "tone", "action"}
    assert [f["headline"] for f in n.context["findings"]] == ["CPU at 97%"]
    assert n.context["analysis"]["headline"] == "A backup job pinned the CPU"
    assert n.context["analysis"]["detail"] == "cron"
    assert "check:web-1:cpu" not in str(n.context)


def test_no_analyze_stage_means_no_analysis():
    assert build_notification(_incident(), {}, "t").context["analysis"] is None


def test_body_reads_top_down():
    previous = {
        "check": {"checks": [CPU_CHECK]},
        "analyze": {"failure": "auth", "fallback_used": True},
    }

    body = build_notification(_incident(), previous, "t").body

    assert body.splitlines()[0] == "CPU at 97%"
    assert "Do this: Find the busiest processes" in body
    assert "Failing checks:\n- CPU at 97%" in body
    assert "AI analysis: AI analysis failed: the provider rejected the API key" in body
    assert "Do this: Update the provider's API key" in body


def test_body_skips_what_is_empty():
    previous = {"analyze": {"summary": "Quiet", "failure": ""}}

    body = build_notification(_incident(status="resolved"), previous, "t").body

    assert "Failing checks" not in body
    assert body.endswith("AI analysis: Quiet")


def test_an_incident_with_no_alerts_has_no_detail():
    incident = Incident.objects.create(severity="warning", subject="x", instance="web-2")

    n = build_notification(incident, {}, "t")

    assert n.body == ""


@override_settings(CONSOLE_URL="https://hub.example.com/")
def test_console_url_when_set():
    incident = _incident()

    n = build_notification(incident, {}, "t")

    assert n.context["console_url"] == f"https://hub.example.com/console/incidents/{incident.pk}/"
    assert n.body.endswith(f"Open in the console: {n.context['console_url']}")


@override_settings(CONSOLE_URL="")
def test_no_console_url_when_unset():
    assert console_url(1) == ""


_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")
_TEMPLATES = (
    "email_text.j2",
    "email_html.j2",
    "incident_notification.j2",
    "slack_text.j2",
    "generic_payload.j2",
    "pagerduty_payload.j2",
)
_HUMAN = ("email_text.j2", "email_html.j2", "incident_notification.j2")


@override_settings(CONSOLE_URL="https://hub.example.com")
@pytest.mark.parametrize("name", _TEMPLATES)
def test_no_identifier_reaches_a_notification(name):
    previous = {
        "check": {"checks": [CPU_CHECK]},
        "analyze": {"failure": "timeout", "fallback_used": True, "error_detail": "read timeout"},
    }
    n = build_notification(_incident(), previous, "trace-1")
    ctx = NotificationTemplatingService().build_template_context(
        {
            "title": n.title,
            "message": n.body,
            "severity": n.severity,
            "channel": "ops",
            "tags": {},
            "context": n.context,
        }
    )

    out = render_template(f"file:{name}", ctx)

    assert "check:web-1:cpu" not in out
    assert "checker:cpu" not in out
    assert "read timeout" not in out
    if name in _HUMAN:
        assert not _SNAKE.findall(out)
