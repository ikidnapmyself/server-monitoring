"""
Local recommendation provider for generating actionable recommendations based on incidents.

This provider analyzes system state and incidents to provide recommendations such as:
- Top memory-consuming processes for memory incidents
- Large files and directories for disk incidents
- Old logs and expired files that can be cleaned up
"""

import os
import shutil
import subprocess
import sys
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import psutil

from apps.intelligence.providers.base import (
    BaseProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)
from config.security import resolve_safe_path

# --- Bounded filesystem scanning ------------------------------------------
# ``manage.py check_health`` completes the pipeline synchronously, so this
# provider runs on an operator's terminal rather than inside a background
# drain, once per incident in the drain. An unbounded walk (from "/", or over
# /var/cache and ~/.cache) is therefore not acceptable: it can take many
# minutes while the operator just waits. Every scan below -- the ``du`` gamble,
# the ``du`` result parse, and the Python walk alike -- is capped on the same
# three axes: wall clock, directory depth, and entries visited. All of them
# return partial results rather than running to completion. A useful answer
# quickly beats a complete one eventually.

# The whole per-analysis budget. One ``ScanBudget`` is opened per disk
# analysis and then shared: the du gamble spends from it, the large-file scan
# spends what is left, and the old-log scan spends what remains after that. So
# this really is the number the operator waits on for one incident -- not a
# per-phase allowance that three phases can each spend in full.
SCAN_TIME_BUDGET_SECONDS = 30.0

# Ceiling on the ``du`` gamble, clamped further to whatever is left of the
# shared budget above. This is not "how long may we spend answering" but "how
# long do we bet on a faster path before giving up on it", and a lost bet
# costs the operator that time for no result at all. ``du`` walks the entire
# filesystem regardless of platform -- GNU's ``--max-depth`` limits output
# depth, not traversal, and BSD's ``-d`` does the same -- so from "/" it
# routinely does not finish. Measured on a developer Mac: ``du -x -d 3 -t 100M
# /`` had not returned after five minutes, while the fallback answered the
# same question in about half a second. So du only has to earn its place when
# it is genuinely quicker: on a small or warm subtree it returns well inside
# this window, and everywhere else we stop betting almost immediately.
DU_TIMEOUT_SECONDS = 3.0

# Directory depth below the scan root; mirrors ``du --max-depth=3``.
SCAN_MAX_DEPTH = 3

# Backstop for a shallow-but-enormous tree, where the depth limit alone does
# not keep the walk short.
SCAN_MAX_ENTRIES = 20_000

# Platforms whose ``du`` is the BSD one. Detection is by ``sys.platform``
# rather than a capability probe: a probe costs an extra subprocess on every
# scan (and would itself need a timeout), while the platform is known for
# free and never changes at runtime. A wrong guess is not fatal either --
# ``du`` exits non-zero and the bounded fallback below takes over.
_BSD_DU_PLATFORMS = ("darwin", "freebsd", "openbsd", "netbsd")


def build_du_command(du_path: str, threshold_mb: int, root_path: str) -> list[str]:
    """Build a depth-bounded ``du`` invocation for the current platform."""
    # ``-k`` on both branches: the parser reads column one as kilobytes, and
    # BSD du reports 512-byte blocks by default -- without it every size is
    # doubled on macOS. GNU du honours ``-k`` too, and it also closes the same
    # hole there under POSIXLY_CORRECT, where GNU defaults to 512-byte blocks.
    if sys.platform.startswith(_BSD_DU_PLATFORMS):
        # BSD du rejects ``--max-depth`` outright, and its synopsis is
        # ``[-a | -s | -d depth]`` -- ``-a`` and ``-d`` are mutually
        # exclusive. Staying bounded matters more than per-file granularity,
        # so drop ``-a`` and report directories only.
        return [
            du_path,
            "-k",
            "-x",
            "-d",
            str(SCAN_MAX_DEPTH),
            "-t",
            f"{threshold_mb}M",
            root_path,
        ]
    return [
        du_path,
        "-ax",
        "-k",
        f"--max-depth={SCAN_MAX_DEPTH}",
        "-t",
        f"{threshold_mb}M",
        root_path,
    ]


@dataclass
class ScanBudget:
    """Shared wall-clock and entry allowance for a single bounded scan."""

    deadline: float
    remaining_entries: int = SCAN_MAX_ENTRIES

    @classmethod
    def start(cls) -> "ScanBudget":
        """Open a budget expiring SCAN_TIME_BUDGET_SECONDS from now.

        Both bounds are read from the module constants at call time, so those
        constants -- not a default frozen at import -- are what an operator
        actually gets.
        """
        return cls(
            deadline=time.monotonic() + SCAN_TIME_BUDGET_SECONDS,
            remaining_entries=SCAN_MAX_ENTRIES,
        )

    @property
    def remaining_seconds(self) -> float:
        """Seconds left before the deadline (negative once it has passed)."""
        return self.deadline - time.monotonic()

    @property
    def exhausted(self) -> bool:
        """True once the entry cap or the wall-clock deadline is reached."""
        return self.remaining_entries <= 0 or time.monotonic() >= self.deadline

    def consume(self) -> None:
        """Charge one visited entry against the budget."""
        self.remaining_entries -= 1


def _safe_iterdir(directory: Path) -> Iterator[Path]:
    """Yield a directory's entries lazily, stopping quietly on an OSError.

    Lazily, because ``list(directory.iterdir())`` spends the time and the
    memory of a whole directory before the caller gets its first entry --- so a
    single huge directory defeats the entry and time caps for exactly the case
    they exist for, and blocks the operator's terminal while it is built.

    Quietly, because an unreadable directory is skipped rather than raised, and
    the read is performed as entries are produced: the ``OSError`` can surface
    at any point during iteration, not only at the ``iterdir()`` call, so the
    guard has to wrap the iteration itself. (``PermissionError`` is an
    ``OSError``, so naming both caught nothing extra.)
    """
    try:
        yield from directory.iterdir()
    except OSError:
        return


def iter_bounded(root: Path, budget: ScanBudget, max_depth: int = SCAN_MAX_DEPTH) -> Iterator[Path]:
    """Yield entries under ``root`` breadth-first, within ``budget``.

    Stays on the root's filesystem, which is what ``du -x`` gave the fast path:
    without it a walk from "/" descends /proc and reports /proc/kcore -- a
    regular file whose ``st_size`` is the physical address space -- as the
    largest thing on disk, and an autofs mountpoint inside the depth limit
    hangs the walk on an automount. Entries on another device are skipped
    entirely, never yielded and never descended into.

    Stops early -- yielding only what it has reached -- when the depth limit,
    the entry cap, or the wall-clock deadline is hit. Each directory is
    consumed lazily (``_safe_iterdir``), so the budget is charged as entries
    arrive rather than after a whole directory has been materialized: a single
    directory of a million entries stops at the cap instead of defeating it.
    Unreadable directories and entries are skipped rather than raised.
    """
    try:
        root_device = root.stat().st_dev
    except (PermissionError, OSError):
        return

    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue:
        directory, depth = queue.popleft()
        if budget.exhausted:
            return
        for entry in _safe_iterdir(directory):
            if budget.exhausted:
                return
            budget.consume()
            try:
                on_root_device = entry.stat().st_dev == root_device
            except (PermissionError, OSError):
                continue
            if not on_root_device:
                continue
            yield entry
            if depth + 1 < max_depth:
                try:
                    descend = entry.is_dir() and not entry.is_symlink()
                except (PermissionError, OSError):
                    continue
                if descend:
                    queue.append((entry, depth + 1))


@dataclass
class ProcessMemoryInfo:
    """Information about a process's memory usage."""

    pid: int
    name: str
    memory_percent: float
    memory_mb: float
    cmdline: str


@dataclass
class LargeFileInfo:
    """Information about a large file or directory."""

    path: str
    size_mb: float
    modified_days_ago: int
    is_directory: bool


@dataclass
class OldFileInfo:
    """Information about an old/expired file."""

    path: str
    size_mb: float
    modified_date: datetime
    days_old: int
    file_type: str  # e.g., 'log', 'cache', 'temp', 'other'


class LocalRecommendationProvider(BaseProvider):
    """
    Local intelligence provider that generates recommendations based on system analysis.

    Features:
    - Memory analysis: finds top memory-consuming processes
    - Disk analysis: finds large files/directories and old logs
    - Provides actionable recommendations for common incidents
    """

    name = "local_recommendation"
    description = "Local system analysis and recommendations"

    # Common log directories to scan
    LOG_DIRECTORIES = [
        "/var/log",
        "/tmp",
        "/var/tmp",
        "~/.cache",
        "/var/cache",
    ]

    # File extensions considered as logs or temporary files
    LOG_EXTENSIONS = {".log", ".log.gz", ".log.1", ".log.2", ".old", ".bak", ".tmp"}
    CACHE_PATTERNS = {"cache", "tmp", "temp", ".cache"}

    def __init__(
        self,
        top_n_processes: int = 10,
        large_file_threshold_mb: float = 100.0,
        old_file_days: int = 30,
        scan_paths: list[str] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        """
        Initialize the local recommendation provider.

        Args:
            top_n_processes: Number of top memory processes to report.
            large_file_threshold_mb: Minimum size in MB to consider a file "large".
            old_file_days: Age in days after which a log file is considered old.
            scan_paths: Custom paths to scan for disk analysis.
            progress_callback: Optional callback function for progress messages.
        """
        self.top_n_processes = top_n_processes
        self.large_file_threshold_mb = large_file_threshold_mb
        self.old_file_days = old_file_days
        self.scan_paths = scan_paths or self.LOG_DIRECTORIES
        self._progress = progress_callback or (lambda msg: None)

    def analyze(
        self, incident: Any | None = None, analysis_type: str = "", path: str = "/"
    ) -> list[Recommendation]:
        """
        Analyze an incident and generate targeted recommendations.

        Args:
            incident: An Incident object from apps.alerts.models.
            analysis_type: Optional type hint for targeted analysis
                (e.g. "memory", "disk"). Bypasses incident detection.
            path: Filesystem path to constrain disk analysis to.

        Returns:
            List of recommendations relevant to the incident.
        """
        # Targeted analysis by type (no incident required)
        if analysis_type == "memory":
            return self._get_memory_recommendations()
        elif analysis_type == "disk":
            return self._get_disk_recommendations(path)

        if incident is None:
            # General system scan (was get_recommendations)
            return self._general_recommendations()

        # Check incident type based on title/description/alerts
        incident_type = self._detect_incident_type(incident)

        if incident_type == "memory":
            recommendations = self._analyze_memory_incident(incident)
        elif incident_type == "disk":
            recommendations = self._analyze_disk_incident(incident)
        elif incident_type == "cpu":
            recommendations = self._analyze_cpu_incident(incident)
        else:
            recommendations = self._general_recommendations()

        return recommendations

    def _general_recommendations(self) -> list[Recommendation]:
        """Get general recommendations based on current system state."""
        recommendations = []

        # Check memory status
        mem = psutil.virtual_memory()
        if mem.percent > 70:
            recommendations.extend(self._get_memory_recommendations())

        # Check disk status
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                if usage.percent > 70:
                    recommendations.extend(self._get_disk_recommendations(partition.mountpoint))
                    break  # Only report once
            except (PermissionError, OSError):
                continue

        return recommendations

    def _detect_incident_type(self, incident: Any) -> str:
        """
        Detect the type of incident based on its title, description, and alerts.

        Args:
            incident: Incident object.

        Returns:
            Incident type: 'memory', 'disk', 'cpu', or 'unknown'.
        """
        # Check title and description
        text = f"{incident.title} {incident.description}".lower()

        # Also check associated alerts
        if hasattr(incident, "alerts"):
            for alert in incident.alerts.all():
                text += f" {alert.name} {alert.description}".lower()

        # Detect type based on keywords
        memory_keywords = {"memory", "ram", "oom", "out of memory", "mem", "swap"}
        disk_keywords = {"disk", "storage", "space", "filesystem", "inode", "quota"}
        cpu_keywords = {"cpu", "load", "processor", "compute"}

        if any(kw in text for kw in memory_keywords):
            return "memory"
        elif any(kw in text for kw in disk_keywords):
            return "disk"
        elif any(kw in text for kw in cpu_keywords):
            return "cpu"

        return "unknown"

    def _analyze_memory_incident(self, incident: Any) -> list[Recommendation]:
        """Generate recommendations for a memory-related incident."""
        return self._get_memory_recommendations(incident_id=incident.id)

    def _analyze_disk_incident(self, incident: Any) -> list[Recommendation]:
        """Generate recommendations for a disk-related incident."""
        recommendations = []

        # Try to extract the affected path from incident metadata or alerts
        affected_path = "/"
        if hasattr(incident, "metadata") and incident.metadata:
            affected_path = incident.metadata.get("path", "/")

        recommendations.extend(
            self._get_disk_recommendations(affected_path, incident_id=incident.id)
        )
        return recommendations

    def _analyze_cpu_incident(self, incident: Any) -> list[Recommendation]:
        """Generate recommendations for a CPU-related incident."""
        return self._get_cpu_recommendations(incident_id=incident.id)

    def _get_memory_recommendations(self, incident_id: int | None = None) -> list[Recommendation]:
        """
        Get memory-related recommendations.

        Returns recommendations about top memory-consuming processes.
        """
        self._progress("Analyzing memory...")
        recommendations = []
        top_processes = self._get_top_memory_processes()
        self._progress(f"Analyzing memory... {len(top_processes)} processes")

        if top_processes:
            self._progress(f"-> Analyzed {len(top_processes)} processes")
            # Calculate total memory used by top processes
            total_mem_percent = sum(p.memory_percent for p in top_processes)

            details = {
                "top_processes": [
                    {
                        "pid": p.pid,
                        "name": p.name,
                        "memory_percent": round(p.memory_percent, 2),
                        "memory_mb": round(p.memory_mb, 2),
                        "cmdline": p.cmdline[:200] if p.cmdline else "",
                    }
                    for p in top_processes
                ],
                "total_memory_percent": round(total_mem_percent, 2),
            }

            # Determine priority based on memory pressure
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                priority = RecommendationPriority.CRITICAL
            elif mem.percent > 80:
                priority = RecommendationPriority.HIGH
            elif mem.percent > 70:
                priority = RecommendationPriority.MEDIUM
            else:
                priority = RecommendationPriority.LOW

            top_3_names = ", ".join(p.name for p in top_processes[:3])

            recommendations.append(
                Recommendation(
                    type=RecommendationType.MEMORY,
                    priority=priority,
                    title="High Memory Usage Detected",
                    description=(
                        f"Top {len(top_processes)} processes are using "
                        f"{total_mem_percent:.1f}% of memory. "
                        f"Top consumers: {top_3_names}"
                    ),
                    details=details,
                    actions=[
                        f"Review process '{top_processes[0].name}' (PID: {top_processes[0].pid}) - using {top_processes[0].memory_percent:.1f}% memory",
                        "Consider restarting memory-heavy services during maintenance window",
                        "Check for memory leaks in long-running processes",
                        "Consider increasing system memory if this is recurring",
                    ],
                    incident_id=incident_id,
                )
            )

        return recommendations

    def _get_disk_recommendations(
        self, path: str = "/", incident_id: int | None = None
    ) -> list[Recommendation]:
        """
        Get disk-related recommendations.

        Scans for large files, old logs, and directories that can be cleaned.
        """
        path = path.strip() if path else "/"
        if not path:
            path = "/"

        recommendations = []

        self._progress(f"Scanning {path}...")
        # One budget for the whole analysis: the du gamble, the large-file
        # scan and the old-log scan all spend from it, so the operator waits
        # SCAN_TIME_BUDGET_SECONDS per incident rather than once per phase.
        budget = ScanBudget.start()
        large_items = self._scan_large_files(path, budget=budget)
        old_files = self._find_old_logs(path, budget=budget)
        self._progress(f"-> Scanned {path}, found {len(large_items)} large items")

        # Large files recommendation
        if large_items:
            total_size_mb = sum(item.size_mb for item in large_items)
            details = {
                "large_items": [
                    {
                        "path": item.path,
                        "size_mb": round(item.size_mb, 2),
                        "modified_days_ago": item.modified_days_ago,
                        "is_directory": item.is_directory,
                    }
                    for item in large_items[:20]  # Limit to top 20
                ],
                "total_size_mb": round(total_size_mb, 2),
            }

            # Determine priority based on disk usage
            try:
                usage = psutil.disk_usage(path)
                if usage.percent > 95:
                    priority = RecommendationPriority.CRITICAL
                elif usage.percent > 90:
                    priority = RecommendationPriority.HIGH
                elif usage.percent > 80:
                    priority = RecommendationPriority.MEDIUM
                else:
                    priority = RecommendationPriority.LOW
            except (OSError, PermissionError):
                priority = RecommendationPriority.MEDIUM

            recommendations.append(
                Recommendation(
                    type=RecommendationType.DISK,
                    priority=priority,
                    title="Large Files and Directories Found",
                    description=(
                        f"Found {len(large_items)} large items totaling "
                        f"{total_size_mb:.1f} MB on {path}"
                    ),
                    details=details,
                    actions=[
                        f"Review largest item: {large_items[0].path} ({large_items[0].size_mb:.1f} MB)",
                        "Run 'ncdu' for interactive disk usage analysis",
                        "Consider archiving or compressing old data",
                        "Set up log rotation if not already configured",
                    ],
                    incident_id=incident_id,
                )
            )

        # Old logs recommendation
        if old_files:
            total_old_size_mb = sum(f.size_mb for f in old_files)
            details = {
                "old_files": [
                    {
                        "path": f.path,
                        "size_mb": round(f.size_mb, 2),
                        "days_old": f.days_old,
                        "file_type": f.file_type,
                    }
                    for f in old_files[:20]  # Limit to top 20
                ],
                "total_size_mb": round(total_old_size_mb, 2),
                "total_files": len(old_files),
            }

            recommendations.append(
                Recommendation(
                    type=RecommendationType.DISK,
                    priority=RecommendationPriority.MEDIUM,
                    title="Old Logs and Temporary Files Found",
                    description=(
                        f"Found {len(old_files)} old files (>{self.old_file_days} days) "
                        f"totaling {total_old_size_mb:.1f} MB that can potentially be cleaned up"
                    ),
                    details=details,
                    actions=[
                        "Review and remove old log files",
                        "Clear old cache directories",
                        "Configure logrotate for automatic log management",
                        f"Run: find /var/log -mtime +{self.old_file_days} -type f -name '*.log*' -ls",
                    ],
                    incident_id=incident_id,
                )
            )

        # If no specific issues found, provide general disk health info
        if not recommendations:
            recommendations.append(
                Recommendation(
                    type=RecommendationType.DISK,
                    priority=RecommendationPriority.LOW,
                    title="Disk Health Check",
                    description="No immediate disk space concerns found.",
                    details={"scanned_path": path},
                    actions=[
                        "Continue monitoring disk usage",
                        "Consider setting up disk usage alerts",
                    ],
                    incident_id=incident_id,
                )
            )

        return recommendations

    def _get_cpu_recommendations(self, incident_id: int | None = None) -> list[Recommendation]:
        """Get CPU-related recommendations."""
        recommendations = []

        # Get top CPU processes
        top_cpu_processes = self._get_top_cpu_processes()

        if top_cpu_processes:
            total_cpu = sum(p["cpu_percent"] for p in top_cpu_processes)

            details = {
                "top_processes": top_cpu_processes,
                "cpu_count": psutil.cpu_count(),
                "cpu_physical_count": psutil.cpu_count(logical=False),
                "load_avg": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
            }

            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent > 90:
                priority = RecommendationPriority.CRITICAL
            elif cpu_percent > 80:
                priority = RecommendationPriority.HIGH
            else:
                priority = RecommendationPriority.MEDIUM

            recommendations.append(
                Recommendation(
                    type=RecommendationType.CPU,
                    priority=priority,
                    title="High CPU Usage Detected",
                    description=(
                        f"Top {len(top_cpu_processes)} processes using significant CPU. "
                        f"Total: {total_cpu:.1f}%"
                    ),
                    details=details,
                    actions=[
                        f"Investigate process '{top_cpu_processes[0]['name']}' (PID: {top_cpu_processes[0]['pid']})",
                        "Check for runaway processes or infinite loops",
                        "Consider process priority adjustments (nice/renice)",
                        "Review cron jobs and scheduled tasks",
                    ],
                    incident_id=incident_id,
                )
            )

        return recommendations

    def _get_top_memory_processes(self) -> list[ProcessMemoryInfo]:
        """Get the top memory-consuming processes."""
        processes = []

        for proc in psutil.process_iter(
            ["pid", "name", "memory_percent", "memory_info", "cmdline"]
        ):
            try:
                mem_percent = proc.info.get("memory_percent") or 0
                mem_info = proc.info.get("memory_info")
                mem_mb = (mem_info.rss / (1024 * 1024)) if mem_info else 0
                cmdline = " ".join(proc.info.get("cmdline") or [])

                if mem_percent > 0.1:  # Filter out very small processes
                    processes.append(
                        ProcessMemoryInfo(
                            pid=proc.info["pid"],
                            name=proc.info["name"] or "unknown",
                            memory_percent=mem_percent,
                            memory_mb=mem_mb,
                            cmdline=cmdline,
                        )
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Sort by memory percentage descending
        processes.sort(key=lambda p: p.memory_percent, reverse=True)
        return processes[: self.top_n_processes]

    def _get_top_cpu_processes(self) -> list[dict[str, Any]]:
        """Get the top CPU-consuming processes."""
        processes = []

        # First call to initialize CPU percent measurement
        for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
            pass

        # Brief pause for accurate measurement
        time.sleep(0.1)

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "cmdline"]):
            try:
                cpu_percent = proc.info.get("cpu_percent") or 0
                if cpu_percent > 1.0:  # Filter low CPU processes
                    processes.append(
                        {
                            "pid": proc.info["pid"],
                            "name": proc.info["name"] or "unknown",
                            "cpu_percent": round(cpu_percent, 2),
                            "cmdline": " ".join(proc.info.get("cmdline") or [])[:200],
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        processes.sort(key=lambda p: p["cpu_percent"], reverse=True)
        return processes[: self.top_n_processes]

    def _scan_large_files(
        self, root_path: str = "/", budget: ScanBudget | None = None
    ) -> list[LargeFileInfo]:
        """
        Scan for large files and directories.

        Uses 'du' command for efficiency when available, falls back to Python.
        Both paths spend from ``budget`` -- the caller's single per-analysis
        allowance -- so neither can run past the operator's advertised wait.
        """
        if root_path != "/":
            root_path = resolve_safe_path(root_path)
        if budget is None:
            budget = ScanBudget.start()
        large_items: list[LargeFileInfo] = []
        threshold_bytes = self.large_file_threshold_mb * 1024 * 1024
        now = datetime.now()

        # Try du first, but only as a short bet on a faster path, capped both
        # by its own ceiling and by what is left of the shared budget; the
        # bounded fallback below is the answer path when the bet does not pay.
        completed: subprocess.CompletedProcess[str] | None = None
        du_path = shutil.which("du")
        if du_path:
            du_seconds = min(DU_TIMEOUT_SECONDS, budget.remaining_seconds)
            if du_seconds > 0:
                try:
                    completed = subprocess.run(  # nosec B603  # nosemgrep
                        build_du_command(du_path, int(self.large_file_threshold_mb), root_path),
                        capture_output=True,
                        text=True,
                        timeout=du_seconds,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
                    completed = None

        if completed is not None and completed.returncode == 0:
            stdout = completed.stdout.strip()
            du_items = self._parse_du_output(stdout, budget, now)
            # A run that produced no usable line from real output is not an
            # all-clear -- it is a du we could not read (busybox, a wrapper,
            # warnings on stdout). Fall through to the bounded walk instead of
            # reporting "no disk space concerns" during a disk incident.
            if du_items or not stdout:
                du_items.sort(key=lambda x: x.size_mb, reverse=True)
                return du_items[:50]  # Limit results

        # Fallback: bounded Python-based scanning (slower but cross-platform).
        # Bounded because this path also runs when du is missing, fails, or
        # times out -- exactly the cases where an unbounded walk would hang.
        try:
            scan_path = Path(root_path).expanduser()
            if not scan_path.exists():
                return large_items

            checked_count = 0
            for item in iter_bounded(scan_path, budget):
                try:
                    if item.is_file():
                        checked_count += 1
                        # Show progress every 100 files to avoid flooding
                        if checked_count % 100 == 0:
                            self._progress(f"Scanning... {checked_count} files")
                        size = item.stat().st_size
                        if size >= threshold_bytes:
                            mtime = datetime.fromtimestamp(item.stat().st_mtime)
                            days_ago = (now - mtime).days
                            size_mb = size / (1024 * 1024)
                            large_items.append(
                                LargeFileInfo(
                                    path=str(item),
                                    size_mb=size_mb,
                                    modified_days_ago=days_ago,
                                    is_directory=False,
                                )
                            )
                            # Report large files found
                            if days_ago > self.old_file_days:
                                self._progress(
                                    f"Found: {str(item)} ({size_mb:.1f} MB, {days_ago} days old)"
                                )
                            else:
                                self._progress(f"Found: {str(item)} ({size_mb:.1f} MB)")
                except (PermissionError, OSError):
                    continue

        except Exception:
            pass

        large_items.sort(key=lambda x: x.size_mb, reverse=True)
        return large_items[:50]

    def _parse_du_output(
        self, stdout: str, budget: ScanBudget, now: datetime
    ) -> list[LargeFileInfo]:
        """Turn ``du`` output into results, charged against ``budget``.

        Each line costs two syscalls (``os.stat`` plus ``os.path.isdir``), so
        this loop is bounded exactly like the Python walk: it stops on the
        entry cap or the deadline and returns what it has.
        """
        items: list[LargeFileInfo] = []
        for line in stdout.split("\n"):
            if not line:
                continue
            if budget.exhausted:
                break
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            budget.consume()
            try:
                size_kb = int(parts[0])
            except ValueError:
                continue
            path = parts[1]
            # du is invoked with -k, so column one is kilobytes on GNU and BSD.
            size_mb = size_kb / 1024

            # Get modification time
            try:
                stat_info = os.stat(path)
                mtime = datetime.fromtimestamp(stat_info.st_mtime)
                days_ago = (now - mtime).days
            except (OSError, PermissionError):
                days_ago = -1

            items.append(
                LargeFileInfo(
                    path=path,
                    size_mb=size_mb,
                    modified_days_ago=days_ago,
                    is_directory=os.path.isdir(path),
                )
            )
            # Report large files found
            if days_ago > self.old_file_days:
                self._progress(f"Found: {path} ({size_mb:.1f} MB, {days_ago} days old)")
            else:
                self._progress(f"Found: {path} ({size_mb:.1f} MB)")

        return items

    def _find_old_logs(
        self, path: str = "/", budget: ScanBudget | None = None
    ) -> list[OldFileInfo]:
        """Find old log files and temporary files that can be cleaned up.

        When a specific path is given (not "/"), scans only that path.
        When path is "/", scans the default log directories.
        """
        if path != "/":
            path = resolve_safe_path(path)
        old_files = []
        cutoff_date = datetime.now() - timedelta(days=self.old_file_days)

        # Use the given path if specific, otherwise fall back to default scan dirs
        scan_dirs = [path] if path != "/" else self.scan_paths

        # One budget shared across every scan directory -- and, when the
        # caller passes one, across the whole disk analysis: the operator
        # waits for the answer, not for each directory or phase in turn.
        if budget is None:
            budget = ScanBudget.start()

        for scan_dir in scan_dirs:
            if budget.exhausted:
                break
            try:
                scan_path = Path(scan_dir).expanduser()
                if not scan_path.exists():
                    continue

                for item in iter_bounded(scan_path, budget):
                    try:
                        if not item.is_file():
                            continue

                        stat_info = item.stat()
                        mtime = datetime.fromtimestamp(stat_info.st_mtime)

                        if mtime < cutoff_date:
                            size_mb = stat_info.st_size / (1024 * 1024)

                            # Only include files > 1MB to reduce noise
                            if size_mb < 1:
                                continue

                            file_type = self._classify_file(item)
                            days_old = (datetime.now() - mtime).days

                            old_files.append(
                                OldFileInfo(
                                    path=str(item),
                                    size_mb=size_mb,
                                    modified_date=mtime,
                                    days_old=days_old,
                                    file_type=file_type,
                                )
                            )
                    except (PermissionError, OSError):
                        continue

            except (PermissionError, OSError):
                continue

        # Sort by size descending
        old_files.sort(key=lambda x: x.size_mb, reverse=True)
        return old_files[:100]  # Limit results

    def _classify_file(self, path: Path) -> str:
        """Classify a file as log, cache, temp, or other."""
        name = path.name.lower()
        suffix = path.suffix.lower()
        path_str = str(path).lower()

        # Check cache patterns first (e.g., /tmp, ~/.cache)
        if any(pattern in path_str for pattern in self.CACHE_PATTERNS):
            return "cache"
        elif suffix in self.LOG_EXTENSIONS or ".log" in name:
            return "log"
        elif suffix in {".tmp", ".temp"} or name.startswith("tmp"):
            return "temp"
        else:
            return "other"


# Convenience function for quick access
def get_local_recommendations(incident=None) -> list[Recommendation]:
    """
    Get recommendations from the local provider.

    Args:
        incident: Optional incident to analyze.

    Returns:
        List of recommendations.
    """
    provider = LocalRecommendationProvider()
    return provider.run(incident=incident)
