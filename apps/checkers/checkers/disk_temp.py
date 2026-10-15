"""Disk temperature checker for Linux (hwmon: drivetemp / nvme).

Reads disk temperatures via ``psutil.sensors_temperatures()`` — no sudo,
provided the ``drivetemp`` kernel module (Linux 5.6+) / ``nvme`` sensors are
loaded. Linux-gated; skips as OK on non-Linux or when no disk sensors are
present. SMART/``smartctl`` (needs root) is intentionally out of scope.

See docs/plans/2026-08-07-thermal-io-checkers-design.md.
"""

import sys
from typing import Any

from apps.checkers.checkers._sensors import TempReading, read_temps
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from config.describing import Action, Description

DISK_CHIPS = {"drivetemp", "nvme"}


class DiskTempChecker(BaseChecker):
    """Report the hottest disk temperature (°C)."""

    name = "disk_temp"
    label = "Disk temperature"
    problem = "Disk running hot"
    warning_threshold = 55.0
    critical_threshold = 60.0

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        readings = read_temps(DISK_CHIPS)
        if not readings:
            return self._skip("no disk temperature sensors")

        hottest = max(readings, key=lambda r: r.current)
        status = self._determine_status(hottest.current)
        message = f"Hottest disk {hottest.current:.1f}°C ({hottest.label}) [{hottest.chip}]"
        return self._make_result(
            status=status,
            message=message,
            metrics=self._metrics(readings, hottest),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.SKIPPED,
            message=f"Skipped: {reason}",
            metrics=self._metrics([], None),
        )

    def _metrics(self, readings: list[TempReading], hottest: TempReading | None) -> dict:
        return {
            "platform": sys.platform,
            "disk_count": len(readings),
            "hottest_c": hottest.current if hottest else None,
            "hottest_disk": hottest.label if hottest else None,
            "disks": [
                {
                    "disk": r.label,
                    "chip": r.chip,
                    "temp_c": r.current,
                    "high_c": r.high,
                    "critical_c": r.critical,
                }
                for r in readings
            ],
        }

    def describe_result(self, status: CheckStatus, metrics: dict[str, Any]) -> Description:
        degrees = round(metrics["hottest_c"])
        return self._describe(
            status,
            f"Hottest disk {metrics['hottest_disk']} at {degrees}°C",
            metrics,
            action=Action(
                "Check the airflow around the disks. A disk that stays hot fails sooner."
            ),
        )
