"""A database holding one incident per case the console describes, for page-wide checks."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.checkers.models import PreflightRun
from apps.notify.models import NotificationChannel
from apps.orchestration.errors import ROUTING_GAPS, StageExecutionError, routing_gap
from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution
from config.failures import FAILURE_KINDS

GAP_DETAILS = {
    "no_route": "nothing in the routing table matched this alert",
    "no_channel": "the matched lane names no active notification channel",
    "no_driver": "the channel's driver is not installed on this hub",
}
FAILURE_STATUS = {
    "auth": 401,
    "rate_limit": 429,
    "timeout": None,
    "provider_down": 503,
    "rejected": 400,
    "unknown": None,
}

CHECK_SNAPSHOT = {
    "checks": [
        {
            "name": "cpu",
            "status": "critical",
            "message": "CPU usage: 97.1%",
            "metrics": {"cpu_percent": 97.1, "cpu_count": 4},
        },
        {
            "name": "memory",
            "status": "warning",
            "message": "Memory usage: 86.0%",
            "metrics": {
                "memory_percent": 86.0,
                "memory_available_gb": 1.1,
                "memory_total_gb": 8.0,
            },
        },
        {"name": "disk_inodes", "status": "ok", "message": "Inodes fine", "metrics": {}},
        {"name": "raid", "status": "skipped", "message": "Skipped: no arrays here", "metrics": {}},
        {
            "name": "network",
            "status": "unknown",
            "message": "Ping is not installed on this host",
            "metrics": {},
        },
        {"name": "gpu_fans", "status": "warning", "message": "Fan two spins slowly"},
    ],
    "checks_run": 6,
    "checks_passed": 1,
    "checks_failed": 4,
    "errors": ["disk_temp: smartctl timed out"],
    "duration_ms": 41.0,
}
ANALYSIS_OK = {
    "summary": "Memory is tight on the hub.",
    "probable_cause": "A worker is leaking memory.",
    "actions": ["Restart the worker"],
    "recommendations": [
        {"priority": "medium", "title": "Add swap", "description": "The box has none."}
    ],
    "confidence": 0.8,
    "fallback_used": False,
    "failure": "",
    "model_info": {"provider": "claude"},
}
DELIVERY_OK = {
    "driver": "ops-slack",
    "provider": "slack",
    "channel": "#ops",
    "status": "success",
    "provider_id": str(uuid.uuid4()),
    "response": {"ok": True},
}


@dataclass(frozen=True)
class World:
    incident_pks: tuple[int, ...]


def _uuid() -> str:
    return str(uuid.uuid4())


def _incident(subject: str, instance: str = "api-1", node: Node | None = None) -> Incident:
    return Incident.objects.create(
        subject=subject, instance=instance, node=node, severity="critical", status="open"
    )


def _alert(incident: Incident, name: str, **fields) -> Alert:
    fields = {"source": "grafana", "severity": "critical", **fields}
    return Alert.objects.create(
        incident=incident,
        fingerprint=_uuid(),
        trace_id=_uuid(),
        name=name,
        status="firing",
        started_at=timezone.now(),
        **fields,
    )


def _run(incident: Incident, status: str = "notified", error: str = "") -> PipelineRun:
    return PipelineRun.objects.create(
        incident=incident,
        run_id=_uuid(),
        trace_id=_uuid(),
        alert_fingerprint=_uuid(),
        status=status,
        last_error_message=error,
        inbound_payload={"downstream_incident_id": incident.pk},
    )


def _stage(run: PipelineRun, stage: str, status: str = "succeeded", **fields) -> StageExecution:
    fields.setdefault("started_at", timezone.now())
    return StageExecution.objects.create(pipeline_run=run, stage=stage, status=status, **fields)


def _checker_incident() -> Incident:
    hub = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
    incident = _incident("High CPU usage", instance=hub.instance_id, node=hub)
    alert = _alert(
        incident,
        "cpu",
        source="cluster",
        node=hub,
        labels={"checker": "cpu"},
        raw_payload={"metrics": {"cpu_percent": 97.1, "cpu_count": 4}},
    )
    _alert(
        incident,
        "gpu_fans",
        source="cluster",
        node=hub,
        severity="warning",
        labels={"checker": "gpu_fans"},
        description="Fan two spins slowly",
    )
    for event, extra in (
        ("created", {"new_status": "firing"}),
        ("resolved", {"old_status": "firing", "new_status": "resolved"}),
        ("refired", {"old_status": "resolved", "new_status": "firing"}),
        ("reevaluated", {"details": {"severity_from": "warning", "severity_to": "critical"}}),
        ("updated", {}),
    ):
        AlertHistory.objects.create(alert=alert, event=event, **extra)
    run = _run(incident)
    _stage(run, "check", output_snapshot=CHECK_SNAPSHOT, duration_ms=41.0)
    return incident


def _webhook_incident() -> Incident:
    incident = _incident("High error rate")
    _alert(
        incident,
        "High error rate",
        description="Five percent of requests fail.",
        annotations={"runbook_url": "https://runbooks.example.com/errors"},
    )
    return incident


def _gap_incident(code: str) -> Incident:
    incident = _incident(f"Routing gap {code.replace('_', ' ')}")
    _alert(incident, "Disk almost full")
    # no_route fails before any stage runs, so only the run records it.
    stage = "routing" if code == "no_route" else "notify"
    gap = routing_gap(stage, code, GAP_DETAILS[code])
    run = _run(incident, status="failed", error=str(gap))
    if code != "no_route":
        _stage(run, stage, status="failed", error_message=gap.errors[0], error_retryable=False)
    return incident


def _retryable_incident() -> Incident:
    incident = _incident("Retryable failure")
    _alert(incident, "Queue backed up")
    run = _run(
        incident,
        status="failed",
        error=str(StageExecutionError("analyze", ["Connection reset by the analysis host"])),
    )
    _stage(
        run,
        "analyze",
        status="failed",
        error_type="ConnectionError",
        error_message="Connection reset by the analysis host",
        error_retryable=True,
    )
    return incident


def _skipped_incident() -> Incident:
    incident = _incident("Skipped stage")
    _alert(incident, "Certificate expires soon")
    _stage(_run(incident), "analyze", status="skipped", error_message="Skipped: no provider is on")
    return incident


def _stalled_incident() -> Incident:
    incident = _incident("Stalled stage")
    _alert(incident, "Backup is late")
    run = _run(incident, status="processing")
    long_ago = timezone.now() - timedelta(minutes=DEFAULT_STALE_MINUTES * 4)
    _stage(run, "check", status="running", started_at=long_ago)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=long_ago)
    return incident


def _analysis_incident(snapshot: dict) -> Incident:
    incident = _incident("Analysis outcome")
    _alert(incident, "Load is climbing")
    _stage(_run(incident), "analyze", output_snapshot=snapshot)
    return incident


def _analysis_failure(kind: str) -> dict:
    return {
        "summary": "",
        "probable_cause": "",
        "actions": [],
        "recommendations": [],
        "confidence": 0.0,
        "fallback_used": True,
        "failure": kind,
        "error_detail": f"Provider said {_uuid()}",
        "model_info": {"provider": "claude"},
    }


def _delivery_incident(delivery: dict) -> Incident:
    incident = _incident("Delivery outcome")
    _alert(incident, "Swap is filling up")
    failed = delivery["status"] != "success"
    snapshot = {
        "deliveries": [delivery],
        "channels_attempted": 1,
        "channels_succeeded": int(not failed),
        "channels_failed": int(failed),
    }
    _stage(_run(incident), "notify", output_snapshot=snapshot)
    return incident


def _delivery_failure(kind: str) -> dict:
    response: dict[str, object] = {"error": f"invalid_auth {_uuid()}"}
    if FAILURE_STATUS[kind] is not None:
        response["status_code"] = FAILURE_STATUS[kind]
    return {
        "driver": "ops-slack",
        "provider": "slack",
        "channel": "#ops",
        "status": "failed",
        "provider_id": "",
        "response": response,
        "failure": kind,
    }


def _readiness_faults() -> None:
    """Lanes that can't deliver, no provider, a failed preflight and a silent peer.

    The stuck run comes from the stalled incident. The channel is active, so the lane
    gaps are reported per lane rather than folded into "no active channel".
    """
    unknown = NotificationChannel.objects.create(
        name="carrier-pigeon", driver="carrier_pigeon", is_active=True
    )
    PipelineDefinition.objects.create(name="pager", stages=["notify"], channel=unknown)
    PipelineDefinition.objects.create(name="escalation", stages=["analyze", "notify"])
    PreflightRun.objects.create(passed=5, warnings=1, errors=2, overall_status="error")
    peer = Node.objects.create(instance_id=_uuid(), hostname="web-2")
    Node.objects.filter(pk=peer.pk).update(last_seen=timezone.now() - timedelta(hours=2))


def build_world() -> World:
    incidents = [
        _checker_incident(),
        _webhook_incident(),
        *(_gap_incident(code) for code in ROUTING_GAPS),
        _retryable_incident(),
        _skipped_incident(),
        _stalled_incident(),
        *(_analysis_incident(_analysis_failure(kind)) for kind in FAILURE_KINDS),
        _analysis_incident(ANALYSIS_OK),
        *(_delivery_incident(_delivery_failure(kind)) for kind in FAILURE_KINDS),
        _delivery_incident(DELIVERY_OK),
    ]
    _readiness_faults()
    return World(incident_pks=tuple(i.pk for i in incidents))
