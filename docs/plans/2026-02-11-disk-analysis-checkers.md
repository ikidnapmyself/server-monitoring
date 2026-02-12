# Disk Analysis Checkers

**Status: Implemented** — All three checkers and tests are in place (20 tests, all passing).

## Goal

Add three new OS-aware disk analysis checkers that identify space hogs, old files, and provide actionable cleanup recommendations.

## New Checkers

### `disk_macos` — macOS only

**OS gate**: `sys.platform == "darwin"`, else early return OK with skip message.

**Lookup paths**:
- `~/Library/Caches/` — per-app caches
- `/Library/Caches/` — system caches
- `~/Library/Logs/` — user logs
- `~/Library/Developer/Xcode/DerivedData/` — Xcode build artifacts
- Homebrew cache (`/Users/*/Library/Caches/Homebrew/` or `$(brew --cache)`)
- `~/Downloads/` — old files (> 30 days)

**Cleanup recommendations**:
- `brew cleanup --prune=all`
- `xcrun simctl delete unavailable`
- Remove stale Xcode DerivedData
- Clear old Downloads

### `disk_linux` — Linux only

**OS gate**: `sys.platform == "linux"`, else early return OK with skip message.

**Lookup paths**:
- `/var/cache/apt/archives/` — apt package cache
- `/var/log/journal/` — systemd journal logs
- `/var/lib/docker/` — Docker data
- `/var/lib/snapd/` — Snap packages
- `/tmp/` — old temp files (> 7 days)

**Cleanup recommendations**:
- `sudo apt clean`
- `sudo journalctl --vacuum-size=100M`
- `docker system prune`
- Remove stale snap revisions

### `disk_common` — All Unix

**OS gate**: None (runs on any Unix platform).

**Lookup paths**:
- `/var/log/` — system logs
- `/tmp/` and `/var/tmp/` — temp files
- `~/.cache/` — user caches (pip, npm, etc.)
- Home directory — large files (> 100MB)

**Cleanup recommendations**:
- Compress/rotate old logs
- Clear old temp files
- `pip cache purge`, `npm cache clean --force`

## Metrics Structure

Each checker returns metrics in this shape:

```python
{
    "platform": "darwin",
    "space_hogs": [
        {"path": "~/Library/Caches/com.apple.Safari", "size_mb": 2048.5},
        {"path": "~/Library/Developer/Xcode/DerivedData", "size_mb": 15360.0},
    ],
    "old_files": [
        {"path": "~/Downloads/archive.zip", "size_mb": 500.0, "age_days": 90},
    ],
    "total_recoverable_mb": 17908.5,
    "recommendations": [
        "Run 'brew cleanup --prune=all' to free Homebrew cache",
        "Remove ~/Library/Developer/Xcode/DerivedData (15.0 GB)",
    ],
}
```

## Status Logic

- `total_recoverable_mb` > `critical_threshold` (default 20000 MB = 20 GB) → CRITICAL
- `total_recoverable_mb` > `warning_threshold` (default 5000 MB = 5 GB) → WARNING
- Otherwise → OK
- OS mismatch → OK with `message="skipped: not applicable for {platform}"`

## Implementation

- Files: `apps/checkers/checkers/disk_macos.py`, `disk_linux.py`, `disk_common.py`
- Tests: `apps/checkers/_tests/checkers/test_disk_macos.py`, etc.
- Registry: add all three to `CHECKER_REGISTRY`
- Use `os.path.expanduser()` for `~` paths
- Use `shutil.disk_usage()` or `os.scandir()` for size calculations (no subprocess calls)
- Timeout: respect `self.timeout` (default 10s), bail out if scanning takes too long