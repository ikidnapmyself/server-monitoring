"""Tests for the reusable inbox drain/reclaim helpers (apps.orchestration.inbox)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.orchestration import inbox
from apps.orchestration.dtos import NotifyResult
from apps.orchestration.executors import AnalyzeExecutor, CheckExecutor, NotifyExecutor
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.testing import clear_lanes


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

    a = Incident.objects.create(severity="critical")
    b = Incident.objects.create(severity="warning")

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


@pytest.mark.django_db
def test_drain_runs_executes_exactly_the_runs_given():
    """Only the listed runs are drained; an unlisted PENDING run is left alone."""
    target = _run("target")
    bystander = _run("bystander")
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain_runs([target])
    assert processed == 1
    assert [call.args[0].run_id for call in mock_exec.call_args_list] == ["target"]
    bystander.refresh_from_db()
    assert bystander.status == PipelineStatus.PENDING
    target.refresh_from_db()
    assert target.status == PipelineStatus.PROCESSING


@pytest.mark.django_db
def test_drain_runs_skips_a_run_it_cannot_claim():
    """A run a concurrent drain already claimed is skipped, not executed twice."""
    run = _run("contended")
    assert inbox.claim(run.pk) is True  # the concurrent drain wins first
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain_runs([run])
    assert processed == 0
    mock_exec.assert_not_called()


@pytest.mark.django_db
def test_drain_runs_uses_the_orchestrator_it_is_given():
    """A caller's own orchestrator (its retry/backoff settings) executes the runs."""
    run = _run("injected")
    executed = []

    class _Recorder:
        def execute_run(self, pipeline_run):
            executed.append(pipeline_run.run_id)

    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        processed = inbox.drain_runs([run], orchestrator=_Recorder())
    assert processed == 1
    assert executed == ["injected"]
    mock_exec.assert_not_called()


@pytest.mark.django_db
def test_drain_runs_returns_the_number_actually_executed():
    """Claimed runs count; ones lost to a concurrent drain do not."""
    won = _run("won")
    lost = _run("lost")
    inbox.claim(lost.pk)
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run"):
        assert inbox.drain_runs([won, lost]) == 1


@pytest.mark.django_db
def test_enqueue_incident_runs_rejects_a_subject_less_id():
    """A run enqueued without an incident is a programming error, not a stored row."""
    from apps.orchestration.models import PipelineOrigin

    with pytest.raises(ValueError, match="incident"):
        inbox.enqueue_incident_runs([None], trace_id="t", origin=PipelineOrigin.MANUAL)
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_enqueue_incident_runs_guard_writes_nothing_at_all():
    """The guard is inside the atomic block: a bad id rolls back its good siblings."""
    from apps.alerts.models import Incident
    from apps.orchestration.models import PipelineOrigin

    good = Incident.objects.create(severity="critical")

    with pytest.raises(ValueError):
        inbox.enqueue_incident_runs([good.id, 0], trace_id="t", origin=PipelineOrigin.MANUAL)
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_a_legacy_ingest_run_still_drains_without_an_incident():
    """The guard is on the incident-enqueue path, not on run creation in general.

    A ``{"driver", "payload"}`` row recorded by the OLD webhook has no incident
    until it ingests, and must still be claimable and executable.
    """
    legacy = PipelineRun.objects.create(
        trace_id="t",
        run_id="legacy",
        status=PipelineStatus.PENDING,
        inbound_payload={"driver": "grafana", "payload": {"alerts": []}},
    )
    assert legacy.incident_id is None

    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        assert inbox.drain_runs([legacy]) == 1
    assert [call.args[0].run_id for call in mock_exec.call_args_list] == ["legacy"]


@pytest.mark.django_db
def test_requeue_wins_once_and_bumps_attempts():
    run = _run("failed", status=PipelineStatus.FAILED)
    stale = timezone.now() - timedelta(hours=2)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=stale)

    assert inbox.requeue(run.pk) is True
    assert inbox.requeue(run.pk) is False

    run.refresh_from_db()
    assert run.status == PipelineStatus.PENDING
    assert run.total_attempts == 2
    assert run.updated_at > stale + timedelta(hours=1)


@pytest.mark.django_db
@pytest.mark.parametrize("status", [s for s in PipelineStatus if s != PipelineStatus.FAILED])
def test_requeue_refuses_a_run_that_is_not_failed(status):
    run = _run("other", status=status)

    assert inbox.requeue(run.pk) is False
    assert inbox.requeue(999_999) is False

    run.refresh_from_db()
    assert (run.status, run.total_attempts) == (status, 1)


@pytest.mark.django_db
@pytest.mark.parametrize("status", inbox.ABANDON_STATUSES)
def test_abandon_fails_an_open_run_once_and_writes_the_error(status):
    run = _run("open", status=status)

    assert inbox.abandon(run.pk) is True
    assert inbox.abandon(run.pk) is False

    run.refresh_from_db()
    assert run.status == PipelineStatus.FAILED
    assert run.last_error_type == "ManualOverride"
    assert run.last_error_message == "Marked failed by an operator"
    assert run.last_error_retryable is False
    assert run.completed_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("status", [s for s in PipelineStatus if s not in inbox.ABANDON_STATUSES])
def test_abandon_refuses_a_finished_run(status):
    run = _run("finished", status=status)

    assert inbox.abandon(run.pk) is False

    run.refresh_from_db()
    assert run.status == status
    assert run.last_error_type == ""


@pytest.mark.django_db
def test_drain_each_counts_runs_that_ran_and_runs_that_raised():
    ok = _run("ok")
    boom = _run("boom")
    busy = _run("busy", status=PipelineStatus.PROCESSING)

    def execute(run):
        if run.run_id == "boom":
            raise RuntimeError("boom")

    with patch(
        "apps.orchestration.inbox.PipelineOrchestrator.execute_run", side_effect=execute
    ) as mock_exec:
        assert inbox.drain_each([ok.pk, boom.pk, busy.pk, 999_999]) == (1, 1)

    assert [call.args[0].run_id for call in mock_exec.call_args_list] == ["ok", "boom"]


@pytest.mark.django_db
def test_pending_pks_are_the_oldest_pending_first():
    newer = _run("newer")
    older = _run("older")
    _run("busy", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )

    assert inbox.pending_pks(10) == [older.pk, newer.pk]
    assert inbox.pending_pks(1) == [older.pk]


@pytest.mark.django_db
def test_a_requeued_run_that_failed_at_notify_reruns_only_notify():
    clear_lanes()
    PipelineDefinition.objects.create(name="all", stages=["check", "analyze", "notify"])
    incident = Incident.objects.create(severity="critical", subject="disk", instance="web-1")
    Alert.objects.create(
        incident=incident,
        fingerprint="fp",
        name="disk",
        severity="critical",
        status="firing",
        source="cluster",
        started_at=timezone.now(),
    )
    run = PipelineRun.objects.create(
        trace_id="t-requeue",
        run_id="r-requeue",
        status=PipelineStatus.FAILED,
        incident=incident,
        inbound_payload={"downstream_incident_id": incident.pk},
    )
    for stage in ("check", "analyze"):
        StageExecution.objects.create(
            pipeline_run=run,
            stage=stage,
            attempt=1,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"summary": stage},
        )
    for attempt in (1, 2, 3):
        StageExecution.objects.create(
            pipeline_run=run, stage="notify", attempt=attempt, status=StageStatus.FAILED
        )

    assert inbox.requeue(run.pk)
    with (
        patch.object(CheckExecutor, "execute") as check,
        patch.object(AnalyzeExecutor, "execute") as analyze,
        patch.object(
            NotifyExecutor, "execute", return_value=NotifyResult(channels_succeeded=1)
        ) as notify,
    ):
        assert inbox.drain_run("r-requeue") == 1

    run.refresh_from_db()
    assert (run.status, run.run_id, run.trace_id) == (
        PipelineStatus.NOTIFIED,
        "r-requeue",
        "t-requeue",
    )
    check.assert_not_called()
    analyze.assert_not_called()
    notify.assert_called_once()
    assert list(
        run.stage_executions.filter(stage="notify")
        .order_by("attempt")
        .values_list("attempt", "status")
    ) == [(1, "failed"), (2, "failed"), (3, "failed"), (4, "succeeded")]
