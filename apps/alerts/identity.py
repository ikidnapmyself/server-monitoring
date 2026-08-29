"""Identity of a checker-origin alert: which machine, which checker.

Both local producers (``check_health`` writing inline, ``push_to_hub``
serialising for a hub) and the node-side push use these, so one condition on
one machine is one Alert row no matter how it arrived.
"""

import socket

from django.conf import settings


def local_instance_id() -> str:
    """This machine's registry key. Falls back to hostname when unconfigured."""
    return getattr(settings, "INSTANCE_ID", "") or socket.gethostname()


def checker_fingerprint(instance_id: str, checker_name: str) -> str:
    """Stable dedup key for a checker result on one machine.

    Keyed on ``instance_id`` rather than hostname: hostnames collide across
    stock installs and change on rename, while the instance id is the Node
    primary key.
    """
    return f"check:{instance_id}:{checker_name}"


def new_fingerprint_for(labels, fallback_instance_id: str, use_hostname: bool = True) -> str | None:
    """Recompute a legacy checker alert's fingerprint from its labels.

    ``None`` when the row is not checker-origin (no ``checker`` label), which is
    how the migration leaves webhook alerts alone. Guards against non-dict
    labels: webhook payloads are attacker-controlled and ``labels`` can be a
    string (same defence as ``instance_key_from_labels`` in services.py).

    ``use_hostname=False`` drops the hostname label from the fallback chain, for
    rows whose hostname label is known not to name the machine that produced
    them. See the caller in migration 0011 for which rows those are.
    """
    if not isinstance(labels, dict):
        return None
    checker = labels.get("checker")
    if not checker:
        return None
    instance = labels.get("instance_id")
    if not instance and use_hostname:
        instance = labels.get("hostname")
    return checker_fingerprint(instance or fallback_instance_id, checker)
