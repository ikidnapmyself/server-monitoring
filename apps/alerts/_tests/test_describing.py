from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.describing import (
    alert_link,
    check_status_of,
    describe_alert,
    describe_history,
    describe_incident,
    describe_incidents,
    history_tone,
)
from apps.alerts.drivers.cluster import ClusterDriver
from apps.alerts.management.commands.push_to_hub import Command, build_cluster_payload
from apps.alerts.models import Alert, Incident, IncidentStatus, Node
from apps.checkers.checkers import CheckResult, CheckStatus
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db

_counter = iter(range(10**6))


def _alert(incident=None, *, age=0, **fields):
    n = next(_counter)
    defaults = {
        "fingerprint": f"fp-{n}",
        "source": "cluster",
        "name": "CPU usage",
        "severity": "critical",
        "status": "firing",
        "description": "",
        "labels": {},
        "annotations": {},
        "raw_payload": {},
        "started_at": timezone.now(),
    }
    defaults.update(fields)
    alert = Alert.objects.create(incident=incident, **defaults)
    if age:
        Alert.objects.filter(pk=alert.pk).update(
            received_at=timezone.now() - timedelta(minutes=age)
        )
        alert.refresh_from_db()
    return alert


def _cpu(incident=None, **fields):
    defaults = {
        "labels": {"checker": "cpu", "instance_id": "i-1"},
        "raw_payload": {"status": "critical", "metrics": {"cpu_percent": 94.0}},
        "description": "CPU usage at 94%",
    }
    defaults.update(fields)
    return _alert(incident, **defaults)


def _incident(**fields):
    defaults = {"severity": "critical", "subject": "checker:cpu", "instance": "i-1"}
    defaults.update(fields)
    return Incident.objects.create(**defaults)


class TestDescribeAlert:
    def test_checker_alert_is_described_by_the_hub_checker(self):
        d = describe_alert(_cpu())
        assert d.headline == "CPU at 94%"
        assert d.tone == "critical"
        assert d.action is not None

    def test_missing_metrics_fall_back_to_the_description(self):
        d = describe_alert(_cpu(raw_payload={"status": "critical"}))
        assert d.headline == "CPU usage at 94%"

    def test_non_dict_payload_falls_back_to_the_description(self):
        d = describe_alert(_cpu(raw_payload="garbage"))
        assert d.headline == "CPU usage at 94%"

    def test_unknown_status_could_not_be_checked(self):
        d = describe_alert(_cpu(raw_payload={"status": "unknown", "metrics": {}}))
        assert d.headline == "CPU usage could not be checked"

    def test_resolved_checker_alert_is_ok_with_no_action(self):
        alert = _cpu(
            status="resolved",
            severity="info",
            raw_payload={"status": "ok", "metrics": {"cpu_percent": 12.0}},
        )
        d = describe_alert(alert)
        assert d.tone == "ok"
        assert d.action is None
        assert d.headline == "CPU at 12%"

    def test_warning_checker_alert(self):
        d = describe_alert(_cpu(severity="warning"))
        assert d.tone == "warning"

    def test_unregistered_checker_uses_its_first_description_line(self):
        alert = _alert(
            name="Zfs pool",
            severity="warning",
            labels={"checker": "zfs_pool"},
            description="Pool tank degraded\nOne disk offline",
        )
        d = describe_alert(alert)
        assert d.headline == "Pool tank degraded"
        assert d.tone == "warning"
        assert d.detail == "Pool tank degraded\nOne disk offline"
        assert Fact("Described by", "the node") in d.facts

    def test_unregistered_checker_without_description_uses_its_label(self):
        d = describe_alert(_alert(labels={"checker": "zfs_pool"}, status="resolved"))
        assert d.headline == "Zfs pool"
        assert d.tone == "ok"

    def test_webhook_alert_keeps_its_name_and_text(self):
        alert = _alert(
            source="alertmanager",
            name="HighLatency",
            severity="warning",
            description="p99 above 2s",
            annotations={"runbook_url": "https://r.example/x"},
        )
        d = describe_alert(alert)
        assert d.headline == "HighLatency"
        assert d.detail == "p99 above 2s"
        assert d.tone == "warning"
        assert d.action == Action("Open the runbook", "open_url", {"url": "https://r.example/x"})

    def test_runbook_annotation_alias(self):
        d = describe_alert(_alert(name="X", annotations={"runbook": "http://r.example/y"}))
        assert d.action.params == {"url": "http://r.example/y"}

    def test_javascript_runbook_gives_no_action(self):
        d = describe_alert(_alert(name="X", annotations={"runbook_url": "javascript:alert(1)"}))
        assert d.action is None

    def test_unnamed_webhook_alert(self):
        d = describe_alert(_alert(name="", labels="not a dict", annotations=["x"]))
        assert d.headline == "Unnamed alert"


class TestReevaluatedListeningPorts:
    def test_firing_alert_without_node_flagged_ports_names_the_allowlist(self):
        alert = _alert(
            severity="warning",
            labels={"checker": "listening_ports", "instance_id": "i-1"},
            raw_payload={
                "status": "firing",
                "metrics": {"listening_count": 4, "unexpected_ports": []},
            },
        )
        d = describe_alert(alert)
        assert d.headline == "Ports open outside this node's allowlist"
        assert d.tone == "warning"

    def test_resolved_alert_with_node_flagged_ports_reads_all_expected(self):
        alert = _alert(
            status="resolved",
            severity="info",
            labels={"checker": "listening_ports", "instance_id": "i-1"},
            raw_payload={
                "status": "firing",
                "metrics": {"listening_count": 4, "unexpected_ports": [8080]},
            },
        )
        d = describe_alert(alert)
        assert d.headline == "4 ports listening, all expected"
        assert "8080" not in d.headline
        assert d.tone == "ok"


class TestCheckStatusOf:
    def test_node_pushed_unknown(self):
        alert = _alert(raw_payload={"status": "firing", "check_status": "unknown"})
        assert check_status_of(alert) is CheckStatus.UNKNOWN

    def test_pushed_unknown_survives_the_cluster_driver(self):
        result = CheckResult(
            status=CheckStatus.UNKNOWN, message="boom", metrics={}, checker_name="cpu"
        )
        payload = build_cluster_payload(
            "i-1", "web-01", [Command()._result_to_alert(result, "i-1", "web-01")]
        )
        parsed = ClusterDriver().parse(payload).alerts[0]
        alert = _alert(
            labels=parsed.labels,
            severity=parsed.severity,
            raw_payload=parsed.raw_payload,
            description=parsed.description,
        )
        assert describe_alert(alert).headline == "CPU usage could not be checked"

    def test_firing_warning(self):
        assert check_status_of(_alert(severity="warning")) is CheckStatus.WARNING

    def test_firing_critical(self):
        assert check_status_of(_alert()) is CheckStatus.CRITICAL


class TestDescribeIncident:
    def test_leads_with_the_worst_firing_alert_on_the_node(self):
        node = Node.objects.create(instance_id="i-1", hostname="web-01")
        incident = _incident(node=node)
        cpu = _cpu(incident, age=5)
        _alert(
            incident,
            severity="warning",
            labels={"checker": "memory", "instance_id": "i-1"},
            raw_payload={"status": "warning", "metrics": {"memory_percent": 81.0}},
        )
        d = describe_incident(incident)
        assert d.headline == "High CPU usage on web-01"
        assert d.detail == "CPU at 94%"
        assert d.action == describe_alert(cpu).action
        assert d.tone == "critical"
        assert d.facts[:3] == (
            Fact("Incident", f"#{incident.pk}", copy=True),
            Fact("Subject", "checker:cpu"),
            Fact("Instance", "i-1"),
        )
        assert Fact("Fingerprint", cpu.fingerprint, copy=True) in d.facts

    def test_node_without_hostname_uses_its_instance_id(self):
        node = Node.objects.create(instance_id="i-9")
        incident = _incident(node=node)
        _cpu(incident)
        assert describe_incident(incident).headline == "High CPU usage on i-9"

    def test_no_node_uses_the_incident_instance(self):
        incident = _incident()
        _cpu(incident)
        assert describe_incident(incident).headline == "High CPU usage on i-1"

    def test_no_instance_drops_the_host(self):
        incident = _incident(instance="")
        _cpu(incident)
        assert describe_incident(incident).headline == "High CPU usage"

    def test_resolved_incident_leads_with_the_newest_alert(self):
        incident = _incident(status=IncidentStatus.RESOLVED)
        _cpu(incident, status="resolved", age=10)
        newest = _alert(
            incident,
            status="resolved",
            labels={"checker": "memory", "instance_id": "i-1"},
            raw_payload={"status": "ok", "metrics": {"memory_percent": 40.0}},
        )
        d = describe_incident(incident)
        assert d.headline == "High memory usage on i-1"
        assert d.tone == "ok"
        assert Fact("Fingerprint", newest.fingerprint, copy=True) in d.facts

    def test_no_alerts(self):
        incident = _incident(instance="")
        assert describe_incident(incident).headline == f"Incident #{incident.pk}"

    def test_no_alerts_with_a_host(self):
        incident = _incident()
        assert describe_incident(incident).headline == f"Incident #{incident.pk} on i-1"

    def test_webhook_incident(self):
        incident = _incident(subject="alertmanager:HighLatency", instance="db-1")
        _alert(incident, source="alertmanager", name="HighLatency", description="p99 above 2s")
        d = describe_incident(incident)
        assert d.headline == "HighLatency on db-1"
        assert d.detail == "p99 above 2s"

    def test_empty_subject_and_instance_give_no_facts(self):
        incident = _incident(subject="", instance="")
        labels = [f.label for f in describe_incident(incident).facts]
        assert "Subject" not in labels and "Instance" not in labels

    def test_unregistered_checker_incident_uses_its_label(self):
        incident = _incident(subject="checker:zfs_pool")
        _alert(incident, labels={"checker": "zfs_pool"}, description="Pool tank degraded")
        d = describe_incident(incident)
        assert d.headline == "Zfs pool on i-1"
        assert d.detail == "Pool tank degraded"


def test_describe_incidents_runs_a_fixed_number_of_queries(django_assert_num_queries):
    incidents = []
    for i in range(5):
        node = Node.objects.create(instance_id=f"i-{i}", hostname=f"web-{i}")
        incident = _incident(node=node, instance=f"i-{i}")
        _cpu(incident)
        _cpu(incident, severity="warning")
        incidents.append(incident)
    with django_assert_num_queries(2):
        described = describe_incidents(incidents)
    assert set(described) == {i.pk for i in incidents}


def _history(event, old_status="", new_status="", severity_from=None, severity_to=None):
    return describe_history(
        event,
        alert_headline="CPU usage",
        old_status=old_status,
        new_status=new_status,
        severity_from=severity_from,
        severity_to=severity_to,
    )


class TestDescribeHistory:
    @pytest.mark.parametrize(
        ("event", "severity_from", "severity_to", "headline"),
        [
            ("created", None, None, "CPU usage started firing"),
            ("resolved", None, None, "CPU usage cleared"),
            ("refired", None, None, "CPU usage fired again"),
            ("reevaluated", "warning", "critical", "CPU usage moved from warning to critical"),
            ("reevaluated", "warning", "warning", "CPU usage updated"),
            ("reevaluated", None, "warning", "CPU usage updated"),
            ("reevaluated", None, None, "CPU usage updated"),
            ("updated", None, None, "CPU usage updated"),
            ("updated", "info", "critical", "CPU usage moved from info to critical"),
            ("severity_changed", "warning", "critical", "CPU usage moved from warning to critical"),
            ("escalated", None, None, "CPU usage changed"),
            ("resolved", "critical", "warning", "CPU usage cleared"),
            ("created", "warning", "critical", "CPU usage started firing"),
            ("refired", "warning", "critical", "CPU usage fired again"),
            ("reevaluated", ["warning"], "critical", "CPU usage updated"),
        ],
    )
    def test_headline(self, event, severity_from, severity_to, headline):
        description = _history(event, severity_from=severity_from, severity_to=severity_to)
        assert description.headline == headline
        assert "→" not in description.headline
        assert "—" not in description.headline

    def test_tone_follows_the_severity_the_row_moved_to(self):
        assert _history("reevaluated", severity_from="info", severity_to="critical").tone == (
            "critical"
        )
        assert _history("resolved").tone == "ok"

    def test_status_and_severity_changes_become_facts(self):
        description = _history(
            "reevaluated",
            old_status="firing",
            new_status="resolved",
            severity_from="warning",
            severity_to="critical",
        )
        assert description.facts == (
            Fact("Status", "firing to resolved"),
            Fact("Severity", "warning to critical"),
        )

    def test_one_sided_values_read_as_the_value_alone(self):
        description = _history("created", new_status="firing", severity_to="warning")
        assert description.facts == (Fact("Status", "firing"), Fact("Severity", "warning"))

    def test_an_old_value_alone_reads_as_the_value(self):
        description = _history("updated", old_status="firing", severity_from="warning")
        assert description.facts == (Fact("Status", "firing"), Fact("Severity", "warning"))

    def test_an_unchanged_value_reads_once(self):
        description = _history(
            "updated",
            old_status="firing",
            new_status="firing",
            severity_from="warning",
            severity_to="warning",
        )
        assert description.facts == (Fact("Status", "firing"), Fact("Severity", "warning"))

    def test_nothing_present_means_no_facts(self):
        assert _history("updated").facts == ()

    def test_non_string_severities_are_left_out(self):
        assert _history("reevaluated", severity_from={"a": 1}, severity_to=["x"]).facts == ()


@pytest.mark.parametrize(
    ("event", "severity_to", "tone"),
    [
        ("created", None, "info"),
        ("refired", None, "info"),
        ("resolved", None, "ok"),
        ("updated", None, "muted"),
        ("reevaluated", "warning", "warning"),
        ("created", "critical", "critical"),
        ("created", "bogus", "info"),
        ("updated", ["critical"], "muted"),
    ],
)
def test_history_tone(event, severity_to, tone):
    assert history_tone(event, severity_to) == tone


def test_alert_link_opens_the_alert():
    assert alert_link(3) == Action("Open the alert", intent="view_alert", params={"pk": 3})
