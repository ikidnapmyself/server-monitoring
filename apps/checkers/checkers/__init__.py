# Checker modules
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from apps.checkers.checkers.cpu import CPUChecker
from apps.checkers.checkers.cpu_temp import CPUTempChecker
from apps.checkers.checkers.disk.common import DiskCommonChecker
from apps.checkers.checkers.disk.linux import DiskLinuxChecker
from apps.checkers.checkers.disk.macos import DiskMacOSChecker
from apps.checkers.checkers.disk.usage import DiskChecker
from apps.checkers.checkers.disk_inodes import DiskInodesChecker
from apps.checkers.checkers.disk_temp import DiskTempChecker
from apps.checkers.checkers.io_strain import IOStrainChecker
from apps.checkers.checkers.listening_ports import ListeningPortsChecker
from apps.checkers.checkers.memory import MemoryChecker
from apps.checkers.checkers.network import NetworkChecker
from apps.checkers.checkers.process import ProcessChecker
from apps.checkers.checkers.raid import RaidChecker
from apps.checkers.checkers.reboot_debian import RebootDebianChecker

__all__ = [
    "BaseChecker",
    "CheckResult",
    "CheckStatus",
    "CPUChecker",
    "CPUTempChecker",
    "MemoryChecker",
    "DiskChecker",
    "DiskCommonChecker",
    "DiskLinuxChecker",
    "DiskInodesChecker",
    "DiskMacOSChecker",
    "DiskTempChecker",
    "IOStrainChecker",
    "ListeningPortsChecker",
    "NetworkChecker",
    "ProcessChecker",
    "RaidChecker",
    "RebootDebianChecker",
    "CHECKER_REGISTRY",
]

# Registry of available checkers
CHECKER_REGISTRY = {
    "cpu": CPUChecker,
    "cpu_temp": CPUTempChecker,
    "memory": MemoryChecker,
    "disk": DiskChecker,
    "disk_common": DiskCommonChecker,
    "disk_linux": DiskLinuxChecker,
    "disk_inodes": DiskInodesChecker,
    "disk_macos": DiskMacOSChecker,
    "disk_temp": DiskTempChecker,
    "io_strain": IOStrainChecker,
    "listening_ports": ListeningPortsChecker,
    "network": NetworkChecker,
    "process": ProcessChecker,
    "raid": RaidChecker,
    "reboot_debian": RebootDebianChecker,
}
