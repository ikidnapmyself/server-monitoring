"""Where a producer hands work to orchestration.

Every producer does the same two things: write alerts and let incidents form,
then enqueue one run per incident that materially changed. This module is the
second half, shared by all four producers — the webhook (``apps.alerts.views``),
a node push (``push_to_hub --local``), this machine's checkers
(``check_health``), and an operator transition
(``IncidentManager._announce``) — so there is exactly one way work enters the
pipeline. Without it each producer grows its own slightly different translation
from "alerts were written" to "runs exist", and they drift.

Whether the caller drains those runs before returning is a mode, not a
different path. A synchronous caller (``check_health``, an operator looking at
a machine over SSH) drains its own; a hub leaves them for ``process_inbox``.
Same rows, same lanes, same executors either way — ``sync`` only decides who
executes them and when.

Imports of ``inbox``/``routing`` are function-level: ``apps.alerts`` calls in
here, and ``inbox`` reaches back into ``apps.alerts`` through the orchestrator,
so keeping the edge inside the call avoids the import cycle. Same convention as
``apps.orchestration.routing``.
"""

import logging

from django.db import transaction

from apps.orchestration.models import PipelineRun

logger = logging.getLogger(__name__)


def enqueue_for(
    result,
    *,
    trace_id: str,
    origin: str,
    source: str = "",
    environment: str = "",
    node=None,
    max_retries: int = 3,
    no_notify: bool = False,
    sync: bool = False,
    orchestrator=None,
) -> list[PipelineRun]:
    """Enqueue one run per materially changed incident in ``result``.

    ``result`` is any producer result exposing ``material_alerts`` — both
    ``ProcessingResult`` and ``CheckAlertResult`` do. Returns the runs created,
    empty when nothing changed materially.

    The attribute is read directly rather than defaulted: a caller passing the
    wrong object is a programming error, and this is the single door every
    producer walks through. An ``AttributeError`` naming the attribute is far
    cheaper than silently enqueueing nothing, whose only symptom is that
    on-call was never told, hours later, with no trace.

    With ``sync=True`` the runs are drained once the enclosing transaction has
    committed, using ``orchestrator`` when the caller has one of its own; its
    retry/backoff settings and executors must apply to these runs rather than
    being silently replaced by a fresh default.

    The enqueue itself is expected to run inside the producer's transaction, so
    that the runs commit with the alert and incident writes that justify them:
    a crash between the two leaves an incident nobody is ever told about, and
    nothing self-heals it — the sender's retry finds the same alert at the same
    severity, so nothing is material, and ``reclaim_stuck`` only rescues rows
    that are already runs.
    """
    from apps.orchestration.inbox import drain_runs, enqueue_incident_runs
    from apps.orchestration.routing import material_incident_ids

    incident_ids = material_incident_ids(result.material_alerts)
    if not incident_ids:
        return []

    runs = enqueue_incident_runs(
        incident_ids,
        trace_id=trace_id,
        origin=origin,
        source=source,
        environment=environment,
        node=node,
        max_retries=max_retries,
        no_notify=no_notify,
    )
    if sync:
        # The drain must never run inside the caller's transaction: it claims
        # runs and executes stages, and rows an uncommitted transaction holds are
        # invisible to every other process, so the claim protects nothing and the
        # work is done against a state that may yet vanish. ``on_commit`` runs the
        # callback immediately when there is no atomic block and defers it to
        # commit when there is, so this is right either way — which matters
        # because whether a producer wrapped us is the producer's business.
        transaction.on_commit(lambda: drain_runs(runs, orchestrator=orchestrator))
    return runs
