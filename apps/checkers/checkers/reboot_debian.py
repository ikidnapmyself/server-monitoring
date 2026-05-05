"""Debian-family reboot-required checker.

Detects /var/run/reboot-required (set by update-notifier-common after
APT installs kernel/libc/systemd updates that require a reboot) and
surfaces it as a WARNING. Stateless — alert lifecycle (open / update /
auto-resolve) is handled by apps.alerts.check_integration.CheckAlertBridge.

See docs/plans/2026-05-05-reboot-debian-checker-design.md for the rationale.
"""

from pathlib import Path

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

REBOOT_FLAG = Path("/var/run/reboot-required")
PKGS_FILE = Path("/var/run/reboot-required.pkgs")
OS_RELEASE = Path("/etc/os-release")


class RebootDebianChecker(BaseChecker):
    """Report WARNING when a Debian-family host has a pending reboot."""

    name = "reboot_debian"

    def check(self) -> CheckResult:
        # Implemented incrementally in subsequent tasks.
        return self._make_result(
            status=CheckStatus.OK,
            message="No reboot required",
            metrics={"reboot_required": False},
        )
