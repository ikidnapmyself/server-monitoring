"""CPU temperature checker for Linux (hwmon: coretemp / k10temp / ...).

Reads CPU package/core temperatures via ``psutil.sensors_temperatures()`` — no
sudo. Linux-gated; skips as OK on non-Linux or when no CPU temperature sensors
are present. Reuses the shared ``_sensors`` helper (see also ``disk_temp``).

See docs/plans/2026-08-07-thermal-io-checkers-design.md.
"""

import sys

from apps.checkers.checkers._sensors import TempReading, read_temps
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

CPU_CHIPS = {"coretemp", "k10temp", "zenpower", "cpu_thermal"}


class CPUTempChecker(BaseChecker):
    """Report the hottest CPU package/core temperature (°C)."""

    name = "cpu_temp"
    warning_threshold = 80.0
    critical_threshold = 90.0

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        readings = read_temps(CPU_CHIPS)
        if not readings:
            return self._skip("no CPU temperature sensors")

        hottest = max(readings, key=lambda r: r.current)
        status = self._determine_status(hottest.current)
        message = f"Hottest CPU sensor {hottest.current:.1f}°C ({hottest.label}) [{hottest.chip}]"
        return self._make_result(
            status=status,
            message=message,
            metrics=self._metrics(readings, hottest),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([], None),
        )

    def _metrics(self, readings: list[TempReading], hottest: TempReading | None) -> dict:
        return {
            "platform": sys.platform,
            "sensor_count": len(readings),
            "hottest_c": hottest.current if hottest else None,
            "hottest_sensor": hottest.label if hottest else None,
            "sensors": [
                {
                    "sensor": r.label,
                    "chip": r.chip,
                    "temp_c": r.current,
                    "high_c": r.high,
                    "critical_c": r.critical,
                }
                for r in readings
            ],
        }
