"""Does the incident follow the alert, and does anyone hear about it?

One table, one place (design doc §2). ``is_material_change`` decides whether an
ALERT changed; this decides what that means for its INCIDENT. Both alert write
paths go through ``AlertOrchestrator._update_alert``, which is the only caller.
"""

from apps.alerts.models import IncidentStatus


def follow_alert(
    incident,
    old_severity: str,
    new_severity: str,
    old_status: str,
    new_status: str,
) -> tuple[bool, bool]:
    """Return ``(reopen, notify)`` for a material alert change under ``incident``.

    - OPEN: notify, nothing to reopen.
    - ACKNOWLEDGED: only an escalation breaks the ack; refires and de-escalations
      are absorbed (history row only).
    - RESOLVED / CLOSED: a firing alert reopens and notifies, whether it refired
      or merely changed severity. An alert going quiet is absorbed.
    """
    from apps.alerts.services import severity_rank

    if incident is None:
        return False, False
    firing = new_status == "firing"
    if incident.status == IncidentStatus.OPEN:
        return False, True
    if incident.status == IncidentStatus.ACKNOWLEDGED:
        escalated = severity_rank(new_severity) > severity_rank(old_severity)
        return (True, True) if escalated else (False, False)
    # RESOLVED / CLOSED
    return (True, True) if firing else (False, False)
