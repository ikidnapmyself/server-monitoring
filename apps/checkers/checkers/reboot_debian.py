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


def _is_debian_family() -> tuple[bool, str]:
    """Return (is_debian_family, distro_id) by reading /etc/os-release.

    Detects Debian, Ubuntu, and any derivative that sets ID_LIKE=debian
    (Mint, Pop!_OS, Kali, Raspbian, etc.). Returns (False, "") on missing
    or unreadable os-release — the caller should treat that as "not
    applicable, skip with OK".
    """
    if not OS_RELEASE.exists():
        return False, ""
    try:
        content = OS_RELEASE.read_text()
    except OSError:
        return False, ""

    fields: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().strip('"').strip("'")

    distro_id = fields.get("ID", "").lower()
    id_like = fields.get("ID_LIKE", "").lower().split()
    is_debian = distro_id in {"debian", "ubuntu"} or "debian" in id_like
    return is_debian, distro_id


class RebootDebianChecker(BaseChecker):
    """Report WARNING when a Debian-family host has a pending reboot."""

    name = "reboot_debian"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip(reason="not Linux", distro_id="")

        is_debian, distro_id = _is_debian_family()
        if not is_debian:
            if distro_id:
                reason = f"not Debian-family ({distro_id})"
            else:
                reason = "cannot determine distro"
            return self._skip(reason=reason, distro_id=distro_id)

        # Reboot-required path implemented in subsequent tasks.
        return self._make_result(
            status=CheckStatus.OK,
            message="No reboot required",
            metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
        )

    def _skip(self, *, reason: str, distro_id: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics(distro_id=distro_id, reboot_required=False, packages=[]),
        )

    def _metrics(
        self,
        *,
        distro_id: str,
        reboot_required: bool,
        packages: list[str],
    ) -> dict:
        return {
            "platform": sys.platform,
            "distro_id": distro_id,
            "reboot_required": reboot_required,
            "pending_packages": packages,
            "pending_package_count": len(packages),
        }
