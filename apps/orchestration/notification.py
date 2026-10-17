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
