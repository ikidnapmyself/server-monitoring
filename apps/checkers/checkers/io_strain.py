"""IO strain checker for Linux (psutil.disk_io_counters busy_time).

Samples per-disk ``busy_time`` over an interval to compute utilization
(``%util``, like ``iostat``). ``compute_io_stats`` is pure so the math is
unit-testable; ``check()`` performs the two sampled reads. Linux-gated; skips
as OK on non-Linux or when no IO counters / ``busy_time`` are available
(``busy_time`` is a Linux field).

See docs/plans/2026-08-07-thermal-io-checkers-design.md.
"""

import sys
import time
from dataclasses import dataclass

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


@dataclass
class DiskIO:
    """Per-disk IO rates over one sample interval."""

    disk: str
    util_percent: float
    read_mb_s: float
    write_mb_s: float
    read_iops: float
    write_iops: float


def compute_io_stats(first: dict, second: dict, elapsed_s: float) -> list[DiskIO]:
    """Per-disk utilization + throughput from two disk_io_counters snapshots.

    ``busy_time`` is milliseconds spent doing IO; ``util_percent`` is the
    fraction of the interval the disk was busy, clamped to [0, 100]. Disks
    absent from ``first`` or lacking ``busy_time`` are skipped. Returns [] when
    ``elapsed_s`` is non-positive.
    """
    stats: list[DiskIO] = []
    if elapsed_s <= 0:
        return stats
    for name, s2 in second.items():
        s1 = first.get(name)
        if s1 is None:
            continue
        busy1 = getattr(s1, "busy_time", None)
        busy2 = getattr(s2, "busy_time", None)
        if busy1 is None or busy2 is None:
            continue
        busy_delta_ms = max(0.0, busy2 - busy1)
        util = min(100.0, busy_delta_ms / (elapsed_s * 1000.0) * 100.0)
        read_bytes = max(0, s2.read_bytes - s1.read_bytes)
        write_bytes = max(0, s2.write_bytes - s1.write_bytes)
        read_ops = max(0, s2.read_count - s1.read_count)
        write_ops = max(0, s2.write_count - s1.write_count)
        stats.append(
            DiskIO(
                disk=name,
                util_percent=round(util, 1),
                read_mb_s=round(read_bytes / elapsed_s / 1_000_000, 2),
                write_mb_s=round(write_bytes / elapsed_s / 1_000_000, 2),
                read_iops=round(read_ops / elapsed_s, 1),
                write_iops=round(write_ops / elapsed_s, 1),
            )
        )
    return stats


class IOStrainChecker(BaseChecker):
    """Report the busiest disk's IO utilization (% busy time)."""

    name = "io_strain"
    warning_threshold = 80.0
    critical_threshold = 95.0

    def __init__(self, sample_interval: float = 1.0, **kwargs) -> None:
        super().__init__(**kwargs)
        if sample_interval <= 0:
            raise ValueError(f"sample_interval must be > 0, got {sample_interval}")
        self.sample_interval = sample_interval

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        first = psutil.disk_io_counters(perdisk=True)
        if not first:
            return self._skip("no disk IO counters")

        start = time.perf_counter()
        time.sleep(self.sample_interval)
        second = psutil.disk_io_counters(perdisk=True)
        elapsed = time.perf_counter() - start

        stats = compute_io_stats(first, second, elapsed)
        if not stats:
            return self._skip("no disk IO utilization data")

        busiest = max(stats, key=lambda d: d.util_percent)
        status = self._determine_status(busiest.util_percent)
        message = f"Busiest disk {busiest.util_percent:.1f}% util ({busiest.disk})"
        return self._make_result(
            status=status,
            message=message,
            metrics=self._metrics(stats, busiest),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([], None),
        )

    def _metrics(self, stats: list[DiskIO], busiest: DiskIO | None) -> dict:
        return {
            "platform": sys.platform,
            "disk_count": len(stats),
            "busiest_disk": busiest.disk if busiest else None,
            "busiest_util_percent": busiest.util_percent if busiest else None,
            "sample_interval_s": self.sample_interval,
            "disks": [
                {
                    "disk": d.disk,
                    "util_percent": d.util_percent,
                    "read_mb_s": d.read_mb_s,
                    "write_mb_s": d.write_mb_s,
                    "read_iops": d.read_iops,
                    "write_iops": d.write_iops,
                }
                for d in stats
            ],
        }
