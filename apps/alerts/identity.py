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
