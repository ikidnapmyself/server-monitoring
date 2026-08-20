"""One rule for "has this alert materially changed?", shared by both ingest paths.

``AlertOrchestrator`` and ``CheckAlertBridge`` are separate create/update paths that
already record different history events (``refired``/``updated`` vs
``severity_changed``). A gate built on those events would behave differently by
origin, and checker traffic — the case the gate exists for — is on the bridge side.
So the predicate lives here and both paths call it.

Deliberately excluded: ``description``. For checker alerts it is
``CheckResult.message``, which carries live metric values and would make every push
look material.
"""


def is_material_change(
    *,
    old_severity: str,
    new_severity: str,
    old_status: str,
    new_status: str,
    old_key: str,
    new_key: str,
) -> bool:
    """True when this update deserves its own downstream pipeline run.

    Symmetric by design: a de-escalation is as material as an escalation, because a
    CRITICAL that fell back to WARNING is news an operator wants.

    ``old_key``/``new_key`` come from :mod:`apps.alerts.context_keys`, where ``""``
    means "this module has nothing to compare" — never "clean". A namespaced empty
    key such as ``"listening_ports:"`` *is* a situation, so no value is special-cased
    here; only ``None`` is folded into ``""``, so that rows written before the column
    existed do not read as a change.
    """
    return (
        old_severity != new_severity
        or old_status != new_status
        or (old_key or "") != (new_key or "")
    )
