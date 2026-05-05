"""Debian-family reboot-required checker.

Detects /var/run/reboot-required (set by update-notifier-common after
APT installs kernel/libc/systemd updates that require a reboot) and
surfaces it as a WARNING. Stateless — alert lifecycle (open / update /
auto-resolve) is handled by apps.alerts.check_integration.CheckAlertBridge.

See docs/plans/2026-05-05-reboot-debian-checker-design.md for the rationale.
"""

import sys
from pathlib import Path

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

REBOOT_FLAG = Path("/var/run/reboot-required")
PKGS_FILE = Path("/var/run/reboot-required.pkgs")
OS_RELEASE = Path("/etc/os-release")


class RebootDebianChecker(BaseChecker):
    """Report WARNING when a Debian-family host has a pending reboot."""

    name = "reboot_debian"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._make_result(
                status=CheckStatus.OK,
                message="Skipped: not Linux",
                metrics={
                    "platform": sys.platform,
                    "distro_id": "",
                    "reboot_required": False,
                    "pending_packages": [],
                    "pending_package_count": 0,
                },
            )
        # Linux path implemented in subsequent tasks.
        return self._make_result(
            status=CheckStatus.OK,
            message="No reboot required",
            metrics={
                "platform": sys.platform,
                "distro_id": "",
                "reboot_required": False,
                "pending_packages": [],
                "pending_package_count": 0,
            },
        )
