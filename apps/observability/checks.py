"""Django system checks for the observability stack.

W001 fires when LOGS_DIR is not writable by the running process. Heartbeat
freshness checks (H001/H002/H003) are added in Phase 3.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core import checks


@checks.register()
def check_logs_dir_writable(app_configs, **kwargs):
    logs_dir = Path(getattr(settings, "LOGS_DIR", ""))
    if not logs_dir:
        return []
    try:
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
        ok = os.access(logs_dir, os.W_OK)
    except OSError:
        ok = False
    if not ok:
        return [
            checks.Warning(
                f"LOGS_DIR is not writable: {logs_dir}",
                hint="Either change LOGS_DIR or grant write access to the application user.",
                id="observability.W001",
            )
        ]
    return []
