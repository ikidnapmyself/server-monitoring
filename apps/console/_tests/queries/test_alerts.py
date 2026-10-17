from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.filters import AlertFilter
from apps.console.queries import alerts as alert_queries
from apps.console.queries.alerts import build_alert_list
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db


def _alert(key, **fields):
    fields = {
        "fingerprint": f"fp-{key}",
        "severity": "warning",
        "status": "firing",
        "source": "cluster",
        "started_at": timezone.now(),
        **fields,
    }
    return Alert.objects.create(name=key, **fields)


def _titles(listing):
    return [row.title for row in listing.rows]


def test_empty_database_says_none():
    listing = build_alert_list(AlertFilter())
    assert listing.rows == () and listing.empty == "none"


def test_default_shows_firing_newest_first_and_says_none_firing_when_quiet():
    _alert("old")
    _alert("new")
    Alert.objects.filter(name="old").update(updated_at=timezone.now() - timedelta(hours=1))
    assert _titles(build_alert_list(AlertFilter())) == ["new", "old"]

    Alert.objects.update(status="resolved")
    assert build_alert_list(AlertFilter()).empty == "none_firing"


def test_status_chips_count_under_the_other_filters():
    _alert("a", severity="critical")
    _alert("b", severity="critical", status="resolved")
    _alert("c")

    listing = build_alert_list(AlertFilter(severity="critical"))
    status = next(g for g in listing.groups if g.name == "status")

    assert {c.key: c.count for c in status.chips} == {"firing": 1, "resolved": 1, "all": 2}


def test_filters_by_severity_source_since_and_node():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    _alert("linked", node=node)
    _alert("labelled", labels={"instance_id": "i-web"})
    _alert("hook", source="grafana", severity="critical")
    _alert("stale")
    Alert.objects.filter(name="stale").update(updated_at=timezone.now() - timedelta(days=3))

    assert _titles(build_alert_list(AlertFilter(severity="critical"))) == ["hook"]
    assert _titles(build_alert_list(AlertFilter(source="grafana"))) == ["hook"]
    assert "stale" not in _titles(build_alert_list(AlertFilter(since="24h")))
    assert set(_titles(build_alert_list(AlertFilter(node=node.pk)))) == {"linked", "labelled"}


def test_unknown_node_or_source_is_dropped_with_a_notice():
    _alert("a")
    listing = build_alert_list(AlertFilter(node=999, source="nowhere"))
    assert _titles(listing) == ["a"]
    assert listing.notice == "Ignored invalid filter: node, source."


def test_search_matches_name_fingerprint_and_trace_ids():
    incident = Incident.objects.create(subject="s", instance="i", severity="warning")
    _alert("disk full", fingerprint="check:i:disk", trace_id="t-alert")
    _alert("cpu", incident=incident)
    PipelineRun.objects.create(incident=incident, run_id="r", trace_id="t-run")

    assert _titles(build_alert_list(AlertFilter(q="DISK"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="check:i"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="t-alert"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="t-run"))) == ["cpu"]
    assert build_alert_list(AlertFilter(q="zzz")).empty == "no_match"


def test_rows_carry_node_incident_and_urls():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = Incident.objects.create(subject="s", instance="i", severity="warning")
    alert = _alert("a", node=node, incident=incident)

    row = build_alert_list(AlertFilter()).rows[0]

    assert (row.node, row.incident_pk) == ("web", incident.pk)
    assert row.url == f"/console/alerts/{alert.pk}/"
    assert row.incident_url == f"/console/incidents/{incident.pk}/"


def test_pages_clamp_and_link(monkeypatch):
    monkeypatch.setattr(alert_queries, "PAGE_SIZE", 1)
    _alert("a")
    _alert("b")

    listing = build_alert_list(AlertFilter(page=9))

    assert (listing.page, listing.pages) == (2, 2)
    assert listing.prev_url == "/console/alerts/"
    assert listing.next_url == ""
