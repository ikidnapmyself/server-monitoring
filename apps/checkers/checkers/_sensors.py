"""Shared hwmon temperature helper (``psutil.sensors_temperatures``).

``parse_temps()`` is pure (no I/O) so it is unit-testable against captured
fixtures; ``read_temps()`` performs the psutil call and delegates to it. Reused
by the ``disk_temp`` and (later) ``cpu_temp`` checkers.
"""

from dataclasses import dataclass

import psutil


@dataclass
class TempReading:
    """One hwmon temperature sensor reading, in degrees Celsius."""

    chip: str
    label: str
    current: float
    high: float | None = None
    critical: float | None = None


def parse_temps(raw: dict, chip_allowlist: set[str]) -> list[TempReading]:
    """Filter a ``sensors_temperatures()`` dict to allowlisted chips.

    Drops readings whose ``current`` is ``None`` or <= 0 (unpopulated sensors).
    """
    readings: list[TempReading] = []
    for chip, entries in raw.items():
        if chip not in chip_allowlist:
            continue
        for entry in entries:
            if entry.current is None or entry.current <= 0:
                continue
            readings.append(
                TempReading(
                    chip=chip,
                    label=entry.label or chip,
                    current=float(entry.current),
                    high=entry.high,
                    critical=entry.critical,
                )
            )
    return readings


def read_temps(chip_allowlist: set[str]) -> list[TempReading]:
    """Read current temperatures for allowlisted chips.

    Returns ``[]`` when ``sensors_temperatures()`` is unavailable (non-Linux)
    or reports nothing for the allowlisted chips.
    """
    fn = getattr(psutil, "sensors_temperatures", None)
    if fn is None:
        return []
    return parse_temps(fn(), chip_allowlist)
