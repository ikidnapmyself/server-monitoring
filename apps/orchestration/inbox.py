"""Reusable inbox drain/reclaim helpers.

The "inbox" is the set of PENDING/PROCESSING ``PipelineRun`` records left by durable
ingest. These pure, importable functions claim and execute those runs. They are the
single source of truth for the claim/drain/reclaim logic shared by the
``process_inbox`` management command and the admin Inbox monitor actions.

The claim is atomic (PENDING -> PROCESSING) so overlapping drains never
double-process, and a crashed drain's PROCESSING runs are reclaimed after the
timeout.
"""

from collections.abc import Iterable
from datetime import timedelta

from django.utils import timezone

from apps.orchestration.models import PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator

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
