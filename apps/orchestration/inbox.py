"""Reusable inbox drain/reclaim helpers.

The "inbox" is the set of PENDING/PROCESSING ``PipelineRun`` records left by durable
ingest. These pure, importable functions claim and execute those runs. They are the
single source of truth for the claim/drain/reclaim logic shared by the
``process_inbox`` management command and the admin Inbox monitor actions. It is
also where every downstream incident run is recorded (``enqueue_incident_runs``).

The claim is atomic (PENDING -> PROCESSING) so overlapping drains never
double-process, and a crashed drain's PROCESSING runs are reclaimed after the
timeout.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.orchestration.models import PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)

# Single source of truth for the stall timeout: how long a run may sit PROCESSING
# before it is considered a crashed/stalled drain and reclaimed. Referenced by
# ``reclaim_stuck``, ``InboxItem.is_stuck``, and the ``process_inbox --stale-minutes``
# default so the literal is not triplicated.
DEFAULT_STALE_MINUTES = 15


def reclaim_stuck(
    timeout_minutes: int = DEFAULT_STALE_MINUTES, pks: Iterable[int] | None = None
) -> int:
    """Return PROCESSING runs stuck past the timeout to PENDING; return the count.

    When ``pks`` is given, only those runs are considered (the admin action scopes the
    reclaim to the operator's selection); with ``pks=None`` the sweep is global (the
    management command's crash-recovery pass).
    """
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    qs = PipelineRun.objects.filter(status=PipelineStatus.PROCESSING, updated_at__lt=cutoff)
    if pks is not None:
        qs = qs.filter(pk__in=pks)
    return qs.update(status=PipelineStatus.PENDING)


def claim(pk: int) -> bool:
    """Atomically move one run PENDING -> PROCESSING. True iff we won the claim."""
    return (
        PipelineRun.objects.filter(pk=pk, status=PipelineStatus.PENDING).update(
            status=PipelineStatus.PROCESSING
        )
        == 1
    )


def _execute(run: PipelineRun) -> None:
    run.refresh_from_db()
    PipelineOrchestrator().execute_run(run)


def drain(limit: int = 50) -> int:
    """Claim and execute up to ``limit`` PENDING runs (oldest first). Return count."""
    # Fetch PKs only (not full rows) so we never load the potentially large
    # ``inbound_payload`` JSON for runs a concurrent drain claims out from under us.
    pending_pks = list(
        PipelineRun.objects.filter(status=PipelineStatus.PENDING)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    processed = 0
    for pk in pending_pks:
        if not claim(pk):
            continue  # a concurrent drain claimed it first
        _execute(PipelineRun(pk=pk))  # refresh_from_db loads the claimed row once
        processed += 1
    return processed


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

    Two producers call this: the alert write path (a node changed an incident) and
    ``IncidentManager`` (a human did). Neither runs anything; ``drain`` is the only
    executor. Left PENDING rather than run inline for the reasons on
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
