"""
Local recommendation provider for generating actionable recommendations based on incidents.

This provider analyzes system state and incidents to provide recommendations such as:
- Top memory-consuming processes for memory incidents
- Large files and directories for disk incidents
- Old logs and expired files that can be cleaned up
"""

import os
import subprocess
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

    def analyze(self, incident: Any | None = None, analysis_type: str = "") -> list[Recommendation]:
        """
        Analyze an incident and generate targeted recommendations.

        Args:
            incident: An Incident object from apps.alerts.models.

        Returns:
            List of recommendations relevant to the incident.
        """
        recommendations = []

        if incident is None:
            return self.get_recommendations()

        # Check incident type based on title/description/alerts
        incident_type = self._detect_incident_type(incident)

        if incident_type == "memory":
            recommendations.extend(self._analyze_memory_incident(incident))
        elif incident_type == "disk":
            recommendations.extend(self._analyze_disk_incident(incident))
        elif incident_type == "cpu":
            recommendations.extend(self._analyze_cpu_incident(incident))
        else:
            # General analysis for unknown incident types
            recommendations.extend(self.get_recommendations())

        return recommendations

    def get_recommendations(self) -> list[Recommendation]:
        """
        Get all current recommendations based on system state.

        Returns:
            List of recommendations for current system issues.
        """
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
        recommendations = []

        self._progress(f"Scanning {path}...")
        # Get large files and directories
        large_items = self._scan_large_files(path)
        old_files = self._find_old_logs()
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
        import time

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

    def _scan_large_files(self, root_path: str = "/") -> list[LargeFileInfo]:
        """
        Scan for large files and directories.

        Uses 'du' command for efficiency when available, falls back to Python.
        """
        large_items = []
        threshold_bytes = self.large_file_threshold_mb * 1024 * 1024
        now = datetime.now()

        # Try using du command for efficiency (if available)
        try:
            result = subprocess.run(
                [
                    "du",
                    "-ax",
                    "--max-depth=3",
                    "-t",
                    f"{int(self.large_file_threshold_mb)}M",
                    root_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        try:
                            size_kb = int(parts[0])
                            path = parts[1]
                            size_mb = size_kb / 1024

                            # Get modification time
                            try:
                                stat_info = os.stat(path)
                                mtime = datetime.fromtimestamp(stat_info.st_mtime)
                                days_ago = (now - mtime).days
                            except (OSError, PermissionError):
                                days_ago = -1

                            large_items.append(
                                LargeFileInfo(
                                    path=path,
                                    size_mb=size_mb,
                                    modified_days_ago=days_ago,
                                    is_directory=os.path.isdir(path),
                                )
                            )
                            # Report large files found
                            if days_ago > self.old_file_days:
                                self._progress(
                                    f"Found: {path} ({size_mb:.1f} MB, {days_ago} days old)"
                                )
                            else:
                                self._progress(f"Found: {path} ({size_mb:.1f} MB)")
                        except ValueError:
                            continue

                large_items.sort(key=lambda x: x.size_mb, reverse=True)
                return large_items[:50]  # Limit results

        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass

        # Fallback: Python-based scanning (slower but cross-platform)
        try:
            scan_path = Path(root_path).expanduser()
            if not scan_path.exists():
                return large_items

            checked_count = 0
            for item in scan_path.rglob("*"):
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

    def _find_old_logs(self) -> list[OldFileInfo]:
        """Find old log files and temporary files that can be cleaned up."""
        old_files = []
        cutoff_date = datetime.now() - timedelta(days=self.old_file_days)

        for scan_dir in self.scan_paths:
            try:
                scan_path = Path(scan_dir).expanduser()
                if not scan_path.exists():
                    continue

                for item in scan_path.rglob("*"):
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
    return provider.analyze(incident)
