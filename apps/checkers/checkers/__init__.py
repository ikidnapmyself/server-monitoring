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
    "CHECKER_REGISTRY",
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
