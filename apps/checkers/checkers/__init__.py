# Checker modules
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.cpu import CPUChecker
from apps.checkers.checkers.disk import DiskChecker
from apps.checkers.checkers.disk_common import DiskCommonChecker
from apps.checkers.checkers.disk_linux import DiskLinuxChecker
from apps.checkers.checkers.disk_macos import DiskMacOSChecker
from apps.checkers.checkers.memory import MemoryChecker
from apps.checkers.checkers.network import NetworkChecker
from apps.checkers.checkers.process import ProcessChecker

__all__ = [
    "BaseChecker",
    "CheckResult",
    "CheckStatus",
    "CPUChecker",
    "MemoryChecker",
    "DiskChecker",
    "DiskCommonChecker",
    "DiskLinuxChecker",
    "DiskMacOSChecker",
    "NetworkChecker",
    "ProcessChecker",
    "get_enabled_checkers",
    "is_checker_enabled",
]

# Registry of available checkers
CHECKER_REGISTRY = {
    "cpu": CPUChecker,
    "memory": MemoryChecker,
    "disk": DiskChecker,
    "disk_common": DiskCommonChecker,
    "disk_linux": DiskLinuxChecker,
    "disk_macos": DiskMacOSChecker,
    "network": NetworkChecker,
    "process": ProcessChecker,
}


def is_checker_enabled(checker_name: str) -> bool:
    """
    Check if a checker is enabled.

    Disabled when:
    - CHECKERS_SKIP_ALL=True, or
    - checker_name is in CHECKERS_SKIP

    Args:
        checker_name: Name of the checker to check.

    Returns:
        True if the checker is enabled, False if skipped.
    """
    from django.conf import settings

    if getattr(settings, "CHECKERS_SKIP_ALL", False):
        return False

    skip_list = getattr(settings, "CHECKERS_SKIP", [])
    return checker_name not in skip_list


def get_enabled_checkers() -> dict[str, type]:
    """
    Get registry of enabled checkers (excluding skipped ones).

    Returns:
        Dictionary of checker names to checker classes, excluding
        those listed in settings.CHECKERS_SKIP.
    """
    return {name: cls for name, cls in CHECKER_REGISTRY.items() if is_checker_enabled(name)}
