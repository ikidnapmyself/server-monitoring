import pytest
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console.queries import alert as alert_query
from apps.console.queries.alert import build_alert_detail

pytestmark = pytest.mark.django_db


def _alert(**fields):
    fields = {
        "fingerprint": "check:i-web:cpu",
        "name": "cpu",
        "severity": "critical",
        "status": "firing",
        "source": "cluster",
        "labels": {"checker": "cpu", "instance_id": "i-web"},
        "raw_payload": {"metrics": {"cpu_percent": 97.1}},
        "trace_id": "t-1",
        "started_at": timezone.now(),
        **fields,
    }
    return Alert.objects.create(**fields)


def test_missing_alert_is_none():
    assert build_alert_detail(999) is None


def test_detail_describes_the_alert_and_its_incident():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = Incident.objects.create(subject="s", instance="i-web", severity="critical")
    alert = _alert(node=node, incident=incident)

    detail = build_alert_detail(alert.pk)

    assert detail.what.headline
    assert detail.node == "web"
    assert detail.incident.pk == incident.pk
    assert detail.incident.url == f"/console/incidents/{incident.pk}/"
    assert {f.label for f in detail.facts} == {"Fingerprint", "Trace ID"}
    assert '"cpu_percent": 97.1' in detail.raw


def test_an_alert_outside_an_incident_has_no_card():
    assert build_alert_detail(_alert().pk).incident is None


def test_reevaluate_is_offered_only_to_a_checker_alert_and_an_editor():
    checker = _alert()
    webhook = _alert(fingerprint="hook", labels={}, source="grafana")

    assert [a.intent for a in build_alert_detail(checker.pk, can_reeval=True).do] == [
        "reevaluate_alert"
    ]
    assert build_alert_detail(checker.pk).do == ()
    assert build_alert_detail(webhook.pk, can_reeval=True).do == ()


def test_labels_that_are_not_a_dict_offer_no_reevaluate():
    alert = _alert(labels=["checker"])
    assert build_alert_detail(alert.pk, can_reeval=True).do == ()


def test_history_is_newest_first_and_capped(monkeypatch):
    monkeypatch.setattr(alert_query, "HISTORY_LIMIT", 2)
    alert = _alert()
    for event in ("created", "reevaluated", "resolved"):
        AlertHistory.objects.create(alert=alert, event=event, new_status="firing")

    detail = build_alert_detail(alert.pk)

    assert len(detail.history) == 2 and detail.hidden == 1
    assert "cleared" in detail.history[0].what.headline


def test_history_survives_details_that_are_not_a_dict():
    alert = _alert()
    AlertHistory.objects.create(alert=alert, event="reevaluated", details=["x"])

    assert len(build_alert_detail(alert.pk).history) == 1


def test_reeval_facts_come_from_the_describer():
    alert = _alert(annotations={})
    assert build_alert_detail(alert.pk).reeval == ()


def test_an_empty_trace_id_is_left_out_of_the_facts():
    alert = _alert(trace_id="")
    assert [f.label for f in build_alert_detail(alert.pk).facts] == ["Fingerprint"]
