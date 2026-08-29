"""Tests for the reusable inbox drain/reclaim helpers (apps.orchestration.inbox)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.orchestration import inbox
from apps.orchestration.models import PipelineRun, PipelineStatus


def _run(run_id, status=PipelineStatus.PENDING):
    return PipelineRun.objects.create(trace_id="t", run_id=run_id, status=status)


@pytest.mark.django_db
def test_reclaim_stuck_moves_stale_processing_to_pending():
    stale = _run("stale", status=PipelineStatus.PROCESSING)
    fresh = _run("fresh", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=stale.pk).update(
        updated_at=timezone.now() - timedelta(minutes=30)
    )
    reclaimed = inbox.reclaim_stuck(timeout_minutes=15)
    assert reclaimed == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == PipelineStatus.PENDING
    assert fresh.status == PipelineStatus.PROCESSING


@pytest.mark.django_db
def test_claim_is_atomic():
    run = _run("claimable")
    assert inbox.claim(run.pk) is True
    # Second claim loses: it is no longer PENDING.
    assert inbox.claim(run.pk) is False
    run.refresh_from_db()
    assert run.status == PipelineStatus.PROCESSING


@pytest.mark.django_db
def test_drain_processes_oldest_first_and_skips_already_claimed():
    older = _run("older")
    _run("newer")
    PipelineRun.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain(limit=10)
    assert processed == 2
    # Oldest executed first.
    executed_run_ids = [call.args[0].run_id for call in mock_exec.call_args_list]
    assert executed_run_ids == ["older", "newer"]


@pytest.mark.django_db
def test_drain_skips_run_claimed_by_a_concurrent_drain():
    _run("a")
    with patch("apps.orchestration.inbox.claim", return_value=False):
        with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
            processed = inbox.drain(limit=10)
    assert processed == 0
    mock_exec.assert_not_called()


@pytest.mark.django_db
def test_drain_run_executes_a_pending_run():
    run = _run("target")
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain_run(run.run_id)
    assert processed == 1
    mock_exec.assert_called_once()
    run.refresh_from_db()
    assert run.status == PipelineStatus.PROCESSING


@pytest.mark.django_db
def test_drain_run_missing_raises_does_not_exist():
    with pytest.raises(PipelineRun.DoesNotExist):
        inbox.drain_run("no-such-run")


@pytest.mark.django_db
def test_drain_run_not_pending_returns_zero():
    run = _run("busy", status=PipelineStatus.PROCESSING)
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain_run(run.run_id)
    assert processed == 0
    mock_exec.assert_not_called()


@pytest.mark.django_db
def test_reclaim_stuck_scoped_to_pks_leaves_others_untouched():
    """A stuck run outside the pks selection is left PROCESSING (scoped reclaim)."""
    selected = _run("selected", status=PipelineStatus.PROCESSING)
    other = _run("other", status=PipelineStatus.PROCESSING)
    stale = timezone.now() - timedelta(minutes=30)
    PipelineRun.objects.filter(pk__in=[selected.pk, other.pk]).update(updated_at=stale)
    reclaimed = inbox.reclaim_stuck(pks=[selected.pk])
    assert reclaimed == 1
    selected.refresh_from_db()
    other.refresh_from_db()
    assert selected.status == PipelineStatus.PENDING
    assert other.status == PipelineStatus.PROCESSING


@pytest.mark.django_db
def test_reclaim_stuck_uses_default_stale_minutes():
    """Calling with no timeout uses DEFAULT_STALE_MINUTES."""
    run = _run("defaulted", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(minutes=inbox.DEFAULT_STALE_MINUTES + 5)
    )
    assert inbox.reclaim_stuck() == 1
    run.refresh_from_db()
    assert run.status == PipelineStatus.PENDING


@pytest.mark.django_db
def test_enqueue_incident_runs_one_pending_run_per_incident_with_given_trace_and_origin():
    from apps.alerts.models import Incident
    from apps.orchestration.models import PipelineOrigin

    a = Incident.objects.create(title="a", severity="critical")
    b = Incident.objects.create(title="b", severity="warning")

    runs = inbox.enqueue_incident_runs(
        [a.id, b.id], trace_id="t-1", origin=PipelineOrigin.MANUAL, source="admin"
    )

    assert len(runs) == 2
    for run, inc in zip(runs, (a, b)):
        assert run.status == PipelineStatus.PENDING
        assert run.trace_id == "t-1"
        assert run.origin == PipelineOrigin.MANUAL
        assert run.incident_id == inc.id
        assert run.inbound_payload == {"downstream_incident_id": inc.id}
    assert len({r.run_id for r in runs}) == 2
    assert PipelineRun.objects.count() == 2


@pytest.mark.django_db
def test_enqueue_incident_runs_empty_list_enqueues_nothing():
    from apps.orchestration.models import PipelineOrigin

    assert inbox.enqueue_incident_runs([], trace_id="t", origin=PipelineOrigin.MANUAL) == []
    assert PipelineRun.objects.count() == 0
