"""Shared utilities for disk analysis checkers."""

import os
import time


def scan_directory(path: str, timeout: float | None = None) -> list[dict]:
    """
    Scan a directory for subdirectories/files and their sizes.
    
    Args:
        path: Directory path to scan
        timeout: Optional timeout in seconds for directory size calculations
        
    Returns:
        List of dicts with 'path' and 'size_mb' keys, sorted by size descending
    """
    results: list[dict] = []
    if not os.path.isdir(path):
        return results
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        size = dir_size(entry.path, timeout=timeout)
                    else:
                        size = entry.stat(follow_symlinks=False).st_size
                    size_mb = size / (1024 * 1024)
                    if size_mb >= 1.0:
                        results.append({"path": entry.path, "size_mb": round(size_mb, 1)})
                except (PermissionError, OSError):
                    # Skip files/dirs we can't access
                    continue
    except (PermissionError, OSError):
        # Skip directories we can't read
        pass
    return sorted(results, key=lambda x: x["size_mb"], reverse=True)


def find_old_files(path: str, max_age_days: int = 7, timeout: float | None = None) -> list[dict]:
    """
    Find files older than max_age_days in the given directory.
    
    Args:
        path: Directory path to scan
        max_age_days: Maximum age in days for files to be considered
        timeout: Optional timeout in seconds to limit scan duration
        
    Returns:
        List of dicts with 'path', 'size_mb', and 'age_days' keys, sorted by size descending
    """
    results: list[dict] = []
    if not os.path.isdir(path):
        return results
    now = time.time()
    cutoff = now - (max_age_days * 86400)
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    stat = entry.stat(follow_symlinks=False)
                    if stat.st_mtime < cutoff:
                        if entry.is_dir(follow_symlinks=False):
                            size = dir_size(entry.path, timeout=timeout)
                        else:
                            size = stat.st_size
                        size_mb = size / (1024 * 1024)
                        age_days = int((now - stat.st_mtime) / 86400)
                        results.append(
                            {
                                "path": entry.path,
                                "size_mb": round(size_mb, 1),
                                "age_days": age_days,
                            }
                        )
                except (PermissionError, OSError):
                    # Skip files/dirs we can't access
                    continue
    except (PermissionError, OSError):
        # Skip directories we can't read
        pass
    return sorted(results, key=lambda x: x["size_mb"], reverse=True)


def find_large_files(
    path: str, min_size_mb: float = 100.0, timeout: float | None = None, exclude_paths: set | None = None
) -> list[dict]:
    """
    Find files larger than min_size_mb in the given directory tree.
    
    Args:
        path: Root directory path to walk
        min_size_mb: Minimum file size in MB
        timeout: Optional timeout in seconds to limit scan duration
        exclude_paths: Set of paths to exclude from the scan
        
    Returns:
        List of dicts with 'path' and 'size_mb' keys, sorted by size descending
    """
    results: list[dict] = []
    if not os.path.isdir(path):
        return results
    
    min_size_bytes = min_size_mb * 1024 * 1024
    exclude_paths = exclude_paths or set()
    
    # Calculate deadline if timeout is provided
    deadline = None
    if timeout and timeout > 0:
        deadline = time.monotonic() + timeout
    
    try:
        for dirpath, _, filenames in os.walk(path):
            # Check timeout before processing each directory
            if deadline is not None and time.monotonic() >= deadline:
                break
            
            # Skip excluded paths
            if any(dirpath.startswith(excl) for excl in exclude_paths):
                continue
                
            for f in filenames:
                # Check timeout for each file
                if deadline is not None and time.monotonic() >= deadline:
                    break
                    
                fp = os.path.join(dirpath, f)
                try:
                    if os.path.islink(fp):
                        continue
                    size = os.path.getsize(fp)
                    if size >= min_size_bytes:
                        results.append(
                            {
                                "path": fp,
                                "size_mb": round(size / (1024 * 1024), 1),
                            }
                        )
                except (PermissionError, OSError):
                    # Skip files we can't access
                    continue
    except (PermissionError, OSError):
        # Skip if we can't walk the directory
        pass
    return sorted(results, key=lambda x: x["size_mb"], reverse=True)


def dir_size(path: str, timeout: float | None = None) -> int:
    """
    Calculate total size of a directory recursively.
    
    Args:
        path: Directory path to measure
        timeout: Optional timeout in seconds to limit scan duration
        
    Returns:
        Total size in bytes
    """
    total = 0
    
    # Calculate deadline if timeout is provided
    deadline = None
    if timeout and timeout > 0:
        deadline = time.monotonic() + timeout
    
    try:
        for dirpath, _, filenames in os.walk(path):
            # Check timeout before processing each directory
            if deadline is not None and time.monotonic() >= deadline:
                break
                
            for f in filenames:
                # Check timeout for each file
                if deadline is not None and time.monotonic() >= deadline:
                    break
                    
                fp = os.path.join(dirpath, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except (PermissionError, OSError):
                    # Skip files we can't access
                    continue
    except (PermissionError, OSError):
        # Skip if we can't walk the directory
        pass
    return total
