import json

from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node


def make_reevaluable_alert(instance_id: str = "web-03") -> Alert:
    """A firing critical CPU alert at 85 whose node's policy re-scores it to warning."""
    node = Node.objects.create(
        instance_id=instance_id,
        hostname=instance_id,
        config={"cpu": {"warning_threshold": 80, "critical_threshold": 99}},
    )
    incident = Incident.objects.create(severity="critical", status="open", node=node)
    return Alert.objects.create(
        fingerprint=f"check:{instance_id}:cpu",
        source="cluster",
        name="cpu high",
        severity="critical",
        status="firing",
        started_at=timezone.now(),
        node=node,
        incident=incident,
        labels={"checker": "cpu", "instance_id": instance_id},
        annotations={"metrics": json.dumps({"cpu_percent": 85.0})},
    )
