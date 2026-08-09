"""Tests for the node/origin backfill data migration.

Exercises the ``forwards`` function directly against the real models: existing
rows get ``origin`` derived from their ``source`` (``cli*`` -> manual, else
incoming_webhook), and ``node`` derived from the incident's alerts (the node FK
lives on ``Alert``, not ``Incident``) when one carries a node.
"""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.orchestration.models import PipelineRun

migration = importlib.import_module("apps.orchestration.migrations.0007_backfill_node_origin")


@pytest.mark.django_db
def test_forwards_sets_manual_origin_for_cli_source():
    run = PipelineRun.objects.create(trace_id="t", run_id="cli-run", source="cli-test")
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "manual"


@pytest.mark.django_db
def test_forwards_sets_incoming_origin_for_other_source():
    run = PipelineRun.objects.create(trace_id="t", run_id="web-run", source="grafana")
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "incoming_webhook"


@pytest.mark.django_db
def test_forwards_leaves_node_null_when_no_alert_has_node():
    """A run whose incident has no node-bearing alert keeps node NULL."""
    from django.utils import timezone

    from apps.alerts.models import Alert, Incident

    incident = Incident.objects.create(title="x", severity="warning")
    Alert.objects.create(
        fingerprint="fp-nonode",
        source="grafana",
        name="A",
        severity="warning",
        status="firing",
        started_at=timezone.now(),
        incident=incident,
    )
    run = PipelineRun.objects.create(
        trace_id="t", run_id="inc-run", source="grafana", incident=incident
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "incoming_webhook"
    assert run.node is None


@pytest.mark.django_db
def test_forwards_copies_node_from_incident_alert():
    """node is derived from the first node-bearing alert of the run's incident."""
    from django.utils import timezone

    from apps.alerts.models import Alert, Incident, Node

    node = Node.objects.create(instance_id="agent-42")
    incident = Incident.objects.create(title="y", severity="critical")
    Alert.objects.create(
        fingerprint="fp-withnode",
        source="grafana",
        name="B",
        severity="critical",
        status="firing",
        started_at=timezone.now(),
        incident=incident,
        node=node,
    )
    run = PipelineRun.objects.create(
        trace_id="t", run_id="inc-node-run", source="grafana", incident=incident
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.node_id == node.id


@pytest.mark.django_db
def test_reverse_is_noop():
    """The reverse migration is a no-op and must not raise."""
    run = PipelineRun.objects.create(trace_id="t", run_id="rev-run", source="cli")
    migration.reverse(django_apps, None)
    run.refresh_from_db()
    # reverse leaves rows untouched
    assert run.run_id == "rev-run"
