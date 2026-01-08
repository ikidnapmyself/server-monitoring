# Checker modules
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.cpu import CPUChecker
from apps.checkers.checkers.disk import DiskChecker
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
    "NetworkChecker",
    "ProcessChecker",
]

# Registry of available checkers
CHECKER_REGISTRY = {
    "cpu": CPUChecker,
    "memory": MemoryChecker,
    "disk": DiskChecker,
    "network": NetworkChecker,
    "process": ProcessChecker,
}
