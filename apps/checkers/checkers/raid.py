"""RAID health checker for Linux software RAID (`/proc/mdstat`).

Two layers live here. A pure parser (`parse_mdstat`) turns the kernel's
`/proc/mdstat` layout into `ArrayState` objects — level, state,
active/total device counts, failed devices, and rebuild/scrub progress —
with no I/O, so it can be unit-tested against captured fixtures. On top,
`RaidChecker` reads the file and maps md-array state onto a `CheckResult`
(OK / WARNING / CRITICAL).

A key distinction drives severity: `recovery`/`resync`/`reshape` are real
REBUILDS that restore redundancy (self-healing → WARNING, and they
suppress CRITICAL while running), whereas `check`/`repair` are read-verify
SCRUBS that never restore redundancy and so must never mask a real
failure.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus

MDSTAT = Path("/proc/mdstat")

# Known non-raidN RAID personalities. A level token is only a level if it
# starts with "raid" (raid0/raid1/raid5/...) or is one of these; otherwise
# the token is a device (inactive arrays list a device first, not a level).
_NON_RAID_LEVELS = {"linear", "multipath", "faulty"}

# Header line: "md0 : active raid1 sdb1[1] sda1[0]"
_HEADER_RE = re.compile(r"^(md\d+)\s*:\s*(\S+)\s+(.*)$")

# A device carrying the (F) faulty flag: "sdd1[3](F)" -> "sdd1".
_FAILED_DEVICE_RE = re.compile(r"(\w+)\[\d+\]\(F\)")

# Device counts "[total/active]": "[3/2]" -> total 3, active 2.
_COUNTS_RE = re.compile(r"\[(\d+)/(\d+)\]")

# Progress line op + percent: "recovery = 50.0%", "check = 12.3%", etc.
# recovery/resync/reshape are real REBUILDS (redundancy is being restored);
# check/repair are read-verify SCRUBS that do not restore redundancy.
_PROGRESS_RE = re.compile(r"(recovery|resync|reshape|check|repair)\s*=\s*(\d+(?:\.\d+)?)%")

# Ops that actually rebuild redundancy (a degraded array doing this is
# self-healing). Everything else the regex matches is a scrub.
_REBUILD_OPS = {"recovery", "resync", "reshape"}


@dataclass
class ArrayState:
    """Parsed state of a single md RAID array."""

    name: str
    state: str
    level: str | None = None
    active_devices: int | None = None
    total_devices: int | None = None
    failed: list[str] = field(default_factory=list)
    rebuilding: bool = False
    scrubbing: bool = False
    resync_percent: float | None = None

    def is_degraded(self) -> bool:
        """Return True if the array is not fully healthy.

        Degraded when the array is not active, has failed devices, or has
        fewer active devices than the total it expects.
        """
        if self.state != "active":
            return True
        if self.failed:
            return True
        if self.active_devices is not None and self.total_devices is not None:
            return self.active_devices < self.total_devices
        return False


def _parse_level(remainder: str) -> str | None:
    """Return the RAID level from a header remainder, or None.

    The first token is a level only when it starts with "raid" or is a
    known non-raidN personality. Inactive arrays list a device token
    first, which is not a level.
    """
    if not remainder:
        return None
    token = remainder.split()[0]
    if token.startswith("raid") or token in _NON_RAID_LEVELS:
        return token
    return None


def _apply_continuation(array: ArrayState, line: str) -> None:
    """Fill counts, up-map, and progress from one continuation line."""
    counts = _COUNTS_RE.search(line)
    if counts:
        array.total_devices = int(counts.group(1))
        array.active_devices = int(counts.group(2))

    progress = _PROGRESS_RE.search(line)
    if progress:
        op = progress.group(1)
        if op in _REBUILD_OPS:
            array.rebuilding = True
        else:
            array.scrubbing = True
        array.resync_percent = float(progress.group(2))


def parse_mdstat(text: str) -> list[ArrayState]:
    """Parse `/proc/mdstat` text into a list of ArrayState objects.

    Defensive against blank, malformed, and unexpected lines: anything
    that is not an md header or an indented continuation line is ignored.
    """
    arrays: list[ArrayState] = []
    current: ArrayState | None = None

    for line in text.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            name, state, remainder = header.groups()
            current = ArrayState(name=name, state=state, level=_parse_level(remainder))
            for device in _FAILED_DEVICE_RE.findall(remainder):
                current.failed.append(device)
            arrays.append(current)
            continue

        # Continuation lines are indented; the first non-indented line
        # (blank included) ends the current array block.
        if current is not None and line[:1].isspace():
            _apply_continuation(current, line)
        else:
            current = None

    return arrays


def _read_mdstat() -> str | None:
    """Return the contents of `/proc/mdstat`, or None if unreadable.

    A missing or unreadable mdstat (no md driver loaded, non-Linux, or a
    permission error) is not a failure — the caller skips with OK.
    """
    try:
        return MDSTAT.read_text()
    except OSError:
        return None


def _serialize_array(array: ArrayState) -> dict:
    """Serialize one ArrayState into a JSON-friendly metrics dict."""
    return {
        "name": array.name,
        "level": array.level,
        "state": array.state,
        "active_devices": array.active_devices,
        "total_devices": array.total_devices,
        "failed": array.failed,
        "rebuilding": array.rebuilding,
        "scrubbing": array.scrubbing,
        "resync_percent": array.resync_percent,
    }


class RaidChecker(BaseChecker):
    """Report software RAID health from Linux `/proc/mdstat`.

    State-based (no numeric thresholds). A degraded array that is *not*
    rebuilding (a dead disk with no recovery in progress, or an
    inactive/failed array) is the CRITICAL emergency. An array that is
    actively rebuilding is self-healing — its counts read as degraded
    (`[2/1] [U_]`) while it recovers — so it is only a WARNING.
    """

    name = "raid"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        text = _read_mdstat()
        if text is None:
            return self._skip("mdstat unavailable")

        arrays = parse_mdstat(text)
        if not arrays:
            return self._make_result(
                status=CheckStatus.OK,
                message="No software RAID arrays",
                metrics=self._metrics(arrays),
            )

        degraded = [a for a in arrays if a.is_degraded()]
        rebuilding = [a for a in arrays if a.rebuilding]
        critical = [a for a in degraded if not a.rebuilding]

        if critical:
            names = ", ".join(a.name for a in critical)
            return self._make_result(
                status=CheckStatus.CRITICAL,
                message=f"Degraded RAID array(s): {names}",
                metrics=self._metrics(arrays),
            )
        if rebuilding:
            names = ", ".join(a.name for a in rebuilding)
            return self._make_result(
                status=CheckStatus.WARNING,
                message=f"RAID array(s) rebuilding: {names}",
                metrics=self._metrics(arrays),
            )
        return self._make_result(
            status=CheckStatus.OK,
            message=f"All {len(arrays)} RAID array(s) healthy",
            metrics=self._metrics(arrays),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([]),
        )

    def _metrics(self, arrays: list[ArrayState]) -> dict:
        return {
            "platform": sys.platform,
            "array_count": len(arrays),
            "arrays": [_serialize_array(a) for a in arrays],
            "degraded_arrays": [a.name for a in arrays if a.is_degraded() and not a.rebuilding],
            "rebuilding_arrays": [a.name for a in arrays if a.rebuilding],
            "scrubbing_arrays": [a.name for a in arrays if a.scrubbing],
        }
