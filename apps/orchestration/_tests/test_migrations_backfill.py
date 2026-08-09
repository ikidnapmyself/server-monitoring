"""Tests for the node/origin backfill data migration.

Exercises the ``forwards`` function directly against the real models: existing
rows get ``origin`` derived from their ``source`` (``cli*`` -> manual, else
incoming_webhook), and ``node`` copied from ``incident.node`` when present.
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
def test_forwards_leaves_node_null_when_incident_has_no_node():
    """A run linked to an incident that carries no node keeps node NULL.

    Incident currently has no ``node`` FK, so the defensive ``getattr`` copy
    branch is a no-op; this asserts the run still backfills its origin safely.
    """
    from apps.alerts.models import Incident

    incident = Incident.objects.create(title="x", severity="warning")
    run = PipelineRun.objects.create(
        trace_id="t", run_id="inc-run", source="grafana", incident=incident
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "incoming_webhook"
    assert run.node is None


@pytest.mark.django_db
def test_reverse_is_noop():
    """The reverse migration is a no-op and must not raise."""
    run = PipelineRun.objects.create(trace_id="t", run_id="rev-run", source="cli")
    migration.reverse(django_apps, None)
    run.refresh_from_db()
    # reverse leaves rows untouched
    assert run.run_id == "rev-run"
