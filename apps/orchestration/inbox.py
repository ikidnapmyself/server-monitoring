"""Reusable inbox drain/reclaim helpers.

The "inbox" is the set of PENDING/PROCESSING ``PipelineRun`` records left by durable
ingest. These pure, importable functions claim and execute those runs. They are the
single source of truth for the claim/drain/reclaim logic shared by the
``process_inbox`` management command and the console's run and inbox pages, and for
the operator's requeue and abandon. It is also where every downstream incident run is
recorded (``enqueue_incident_runs``).

The claim is atomic (PENDING -> PROCESSING) so overlapping drains never
double-process, and a crashed drain's PROCESSING runs are reclaimed after the
timeout.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.orchestration.models import PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)

# Single source of truth for the stall timeout: how long a run may sit PROCESSING
# before it is considered a crashed/stalled drain and reclaimed. Referenced by
# ``reclaim_stuck``, ``InboxItem.is_stuck``, and the ``process_inbox --stale-minutes``
# default so the literal is not triplicated.
DEFAULT_STALE_MINUTES = 15

ABANDON_STATUSES = (PipelineStatus.PENDING, PipelineStatus.PROCESSING, PipelineStatus.RETRYING)
ABANDON_MESSAGE = "Marked failed by an operator"


def reclaim_stuck(timeout_minutes: int = DEFAULT_STALE_MINUTES) -> int:
    """Return PROCESSING runs stuck past the timeout to PENDING; return the count."""
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    return PipelineRun.objects.filter(
        status=PipelineStatus.PROCESSING, updated_at__lt=cutoff
    ).update(status=PipelineStatus.PENDING, updated_at=timezone.now())


def claim(pk: int) -> bool:
    """Atomically move one run PENDING -> PROCESSING. True iff we won the claim.

    ``update`` skips ``auto_now``, so ``updated_at`` is set here. Otherwise a run that
    waited past the stall timeout would read as stuck the instant it is claimed, and a
    concurrent ``reclaim_stuck`` could hand it to a second drain.
    """
    return (
        PipelineRun.objects.filter(pk=pk, status=PipelineStatus.PENDING).update(
            status=PipelineStatus.PROCESSING, updated_at=timezone.now()
        )
        == 1
    )


def _execute(run: PipelineRun) -> None:
    run.refresh_from_db()
    PipelineOrchestrator().execute_run(run)


def pending_pks(limit: int) -> list[int]:
    """The pks of up to ``limit`` PENDING runs, oldest first."""
    # Fetch PKs only (not full rows) so we never load the potentially large
    # ``inbound_payload`` JSON for runs a concurrent drain claims out from under us.
    return list(
        PipelineRun.objects.filter(status=PipelineStatus.PENDING)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )


def drain(limit: int = 50) -> int:
    """Claim and execute up to ``limit`` PENDING runs (oldest first). Return count."""
    processed = 0
    for pk in pending_pks(limit):
        if not claim(pk):
            continue  # a concurrent drain claimed it first
        _execute(PipelineRun(pk=pk))  # refresh_from_db loads the claimed row once
        processed += 1
    return processed


def drain_each(pks: Iterable[int]) -> tuple[int, int]:
    """Claim and execute each run on its own. Return how many ran and how many raised.

    One run that raises is logged and counted, and the rest still run. A run already
    claimed, or gone, is neither.
    """
    ran = failed = 0
    for pk in pks:
        try:
            if claim(pk):
                _execute(PipelineRun(pk=pk))
                ran += 1
        except Exception:  # noqa: BLE001
            logger.exception("Drain failed for run pk=%s", pk)
            failed += 1
    return ran, failed


def requeue(pk: int) -> bool:
    """Put a failed run back in the queue. True iff this call moved it.

    The next drain claims it and ``execute_run`` skips every stage that already succeeded,
    so it keeps its ``run_id`` and ``trace_id`` and re-runs only what failed. ``update``
    skips ``auto_now``, and ``reclaim_stuck`` reads ``updated_at``, so it is set here.
    """
    return (
        PipelineRun.objects.filter(pk=pk, status=PipelineStatus.FAILED).update(
            status=PipelineStatus.PENDING,
            total_attempts=F("total_attempts") + 1,
            updated_at=timezone.now(),
        )
        == 1
    )


def abandon(pk: int) -> bool:
    """Fail a queued, claimed or retrying run that should never run. True iff this call did."""
    now = timezone.now()
    return (
        PipelineRun.objects.filter(pk=pk, status__in=ABANDON_STATUSES).update(
            status=PipelineStatus.FAILED,
            last_error_type="ManualOverride",
            last_error_message=ABANDON_MESSAGE,
            last_error_retryable=False,
            completed_at=now,
            updated_at=now,
        )
        == 1
    )


def drain_run(run_id: str) -> int:
    """Process one specific run by run_id.

    Raises ``PipelineRun.DoesNotExist`` if no such run exists (callers translate).
    Returns 0 if the run is not PENDING (already claimed / drained), else 1.
    """
    run = PipelineRun.objects.get(run_id=run_id)
    if not claim(run.pk):
        return 0
    _execute(run)
    return 1


def drain_runs(runs, *, orchestrator=None) -> int:
    """Claim and execute exactly these runs. Return how many actually ran.

    This is not ``drain``: it is scoped to work the caller already knows about —
    the runs it just enqueued — rather than sweeping whatever the queue happens to
    hold. Synchronous callers (``manage.py run_pipeline``, CLI diagnostics, tests)
    expect one call to carry the whole pipeline through, and after fan-out
    everything downstream of the entry stage lives in the children, so those
    children have to be executed here. ``execute_run`` deliberately does not do it
    itself, because ``process_inbox`` is already the drain and would recurse.

    Claimed the same way ``drain`` claims, so a concurrent ``process_inbox`` can
    never double-execute a run; anything we do not win is skipped.

    ``orchestrator`` is injectable because the caller's retry/backoff settings and
    executors must apply to these runs too — ``drain_run``'s fresh orchestrator
    would silently drop them. It defaults to a fresh ``PipelineOrchestrator`` for
    callers with nothing of their own to pass.
    """
    orchestrator = orchestrator if orchestrator is not None else PipelineOrchestrator()
    processed = 0
    for run in runs:
        if not claim(run.pk):
            continue  # a concurrent drain claimed it first
        run.refresh_from_db()  # load the claimed row's current state once
        orchestrator.execute_run(run)
        processed += 1
    return processed


def enqueue_incident_runs(
    incident_ids,
    *,
    trace_id: str,
    origin: str,
    source: str = "",
    environment: str = "",
    node=None,
    max_retries: int = 3,
    parent_run_id: str = "",
    no_notify: bool = False,
) -> list[PipelineRun]:
    """Record one PENDING run per incident — the ONE way an incident change reaches on-call.

    Three producers call this: the alert write path (a node changed an incident),
    ``IncidentManager`` (a human did) and ``apps.alerts.reeval_existing`` (an applied
    re-evaluation did). None of them runs anything; ``drain`` is the only executor.
    Left PENDING rather than run inline for the reasons on
    ``PipelineOrchestrator._enqueue_downstream_runs``.

    ``no_notify`` travels with the work. NOTIFY runs in the child, not in the run
    the operator invoked, so ``run_pipeline --no-notify`` would silence nothing if
    the flag stopped at the parent. Written only when set, so an ordinary child's
    stored payload is byte-identical to what it always was.
    """
    child_payload: dict = {"no_notify": True} if no_notify else {}
    runs: list[PipelineRun] = []
    with transaction.atomic():
        for incident_id in incident_ids:
            # Every run enqueued here has an incident as its subject. The database
            # column stays NULLABLE on purpose — rows predating this refactor were
            # written by the entry stage, before an incident existed, and history
            # must keep rendering — so the requirement lives here, at the one door
            # new work comes through. A subject-less enqueue is a programming
            # error: the run would resolve no lane, notify nobody, and say so only
            # by on-call never hearing about it.
            if not incident_id:
                raise ValueError(
                    f"enqueue_incident_runs requires an incident id, got {incident_id!r}"
                )
            runs.append(
                PipelineRun.objects.create(
                    trace_id=trace_id,
                    run_id=str(uuid.uuid4()),
                    source=source,
                    environment=environment,
                    status=PipelineStatus.PENDING,
                    max_retries=max_retries,
                    inbound_payload=child_payload | {"downstream_incident_id": incident_id},
                    origin=origin,
                    node=node,
                    incident_id=incident_id,
                )
            )
    if runs:
        logger.info(
            "Enqueued %d incident run(s) for trace_id=%s",
            len(runs),
            trace_id,
            extra={"trace_id": trace_id, "run_id": parent_run_id},
        )
    return runs
