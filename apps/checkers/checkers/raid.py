"""RAID health parser for Linux software RAID (`/proc/mdstat`).

Pure parsing of the kernel's `/proc/mdstat` layout into `ArrayState`
objects. Each md array is reported with its level, state, active/total
device counts, failed devices, and rebuild/resync progress.

This module is parser-only: it does no I/O and produces no side effects,
so it can be unit-tested against captured `/proc/mdstat` fixtures. The
`RaidChecker` class that reads the file and maps state onto a
`CheckResult` lives in a later chunk.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

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

# Rebuild/resync progress: "recovery = 50.0%" or "recovery = 100%".
_PROGRESS_RE = re.compile(r"(recovery|resync|reshape|check)\s*=\s*(\d+(?:\.\d+)?)%")


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
        array.rebuilding = True
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
