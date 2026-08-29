"""Tests for the shared producer intake (apps.orchestration.intake)."""

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, AlertStatus, Incident
from apps.orchestration.intake import enqueue_for
from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus


class _Result:
    """Minimal stand-in: any producer result exposing material_alerts."""

    def __init__(self, material_alerts):
        self.material_alerts = material_alerts


def _alert(fingerprint, incident=None, severity="critical"):
    return Alert.objects.create(
        fingerprint=fingerprint,
        source="cluster",
        name="CPU Check Alert",
        severity=severity,
        status=AlertStatus.FIRING,
        incident=incident,
        started_at=timezone.now(),
    )


@pytest.mark.django_db
def test_enqueues_one_pending_run_per_material_incident():
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = _alert("check:n1:cpu", incident=incident)

    runs = enqueue_for(_Result([alert]), trace_id="t1", origin=PipelineOrigin.INCOMING_WEBHOOK)

    assert len(runs) == 1
    assert runs[0].status == PipelineStatus.PENDING
    assert runs[0].incident_id == incident.id
    assert runs[0].trace_id == "t1"
    assert runs[0].origin == PipelineOrigin.INCOMING_WEBHOOK


@pytest.mark.django_db
def test_two_alerts_of_one_incident_enqueue_one_run():
    """One situation is one unit of work, however many alerts carried it."""
    incident = Incident.objects.create(title="cpu", severity="critical")
    alerts = [_alert("check:n1:cpu", incident=incident), _alert("check:n1:load", incident=incident)]

    runs = enqueue_for(_Result(alerts), trace_id="t1", origin=PipelineOrigin.CHECKER_GENERATED)

    assert len(runs) == 1
    assert runs[0].incident_id == incident.id
    assert PipelineRun.objects.count() == 1


@pytest.mark.django_db
def test_an_alert_with_no_incident_enqueues_nothing():
    alert = _alert("check:n1:cpu", severity="warning")

    assert enqueue_for(_Result([alert]), trace_id="t1", origin=PipelineOrigin.MANUAL) == []
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_no_material_alerts_enqueues_nothing():
    assert enqueue_for(_Result([]), trace_id="t1", origin=PipelineOrigin.MANUAL) == []
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_sync_leaves_nothing_pending():
    """A synchronous caller expects one call to finish the job."""
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = _alert("check:n1:cpu", incident=incident)

    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        runs = enqueue_for(
            _Result([alert]), trace_id="t1", origin=PipelineOrigin.CHECKER_GENERATED, sync=True
        )

    assert len(runs) == 1
    mock_exec.assert_called_once()
    assert not PipelineRun.objects.filter(trace_id="t1", status=PipelineStatus.PENDING).exists()


@pytest.mark.django_db
def test_without_sync_the_runs_are_left_pending():
    """The hub case: process_inbox will take them."""
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = _alert("check:n1:cpu", incident=incident)

    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        runs = enqueue_for(_Result([alert]), trace_id="t1", origin=PipelineOrigin.INCOMING_WEBHOOK)

    mock_exec.assert_not_called()
    assert [r.status for r in runs] == [PipelineStatus.PENDING]
    assert PipelineRun.objects.filter(trace_id="t1", status=PipelineStatus.PENDING).count() == 1


@pytest.mark.django_db
def test_sync_uses_the_orchestrator_it_is_given():
    """A synchronous caller's retry/backoff settings must reach the runs."""
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = _alert("check:n1:cpu", incident=incident)
    executed = []

    class _Recorder:
        def execute_run(self, pipeline_run):
            executed.append(pipeline_run.incident_id)

    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        enqueue_for(
            _Result([alert]),
            trace_id="t1",
            origin=PipelineOrigin.MANUAL,
            sync=True,
            orchestrator=_Recorder(),
        )

    assert executed == [incident.id]
    mock_exec.assert_not_called()


@pytest.mark.django_db
def test_optional_fields_travel_to_the_enqueued_run():
    """source/environment/max_retries/no_notify are recorded on the run."""
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = _alert("check:n1:cpu", incident=incident)

    runs = enqueue_for(
        _Result([alert]),
        trace_id="t1",
        origin=PipelineOrigin.MANUAL,
        source="admin",
        environment="prod",
        max_retries=1,
        no_notify=True,
    )

    assert runs[0].source == "admin"
    assert runs[0].environment == "prod"
    assert runs[0].max_retries == 1
    assert runs[0].inbound_payload == {"no_notify": True, "downstream_incident_id": incident.id}


@pytest.mark.django_db
def test_a_result_without_material_alerts_raises():
    """The wrong object is a programming error, not a silent no-op.

    Enqueueing nothing would surface only as "on-call was never told", hours
    later and with no trace; an AttributeError naming the attribute is cheap.
    """

    class _NotAResult:
        pass

    with pytest.raises(AttributeError, match="material_alerts"):
        enqueue_for(_NotAResult(), trace_id="t1", origin=PipelineOrigin.MANUAL)
    assert PipelineRun.objects.count() == 0
