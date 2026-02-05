# Progress Messages for `get_recommendations` Command

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add verbose progress messages to the `get_recommendations` command showing what's being scanned in real-time.

**Architecture:** Callback pattern - the LocalRecommendationProvider accepts an optional `progress_callback` function and calls it during scanning operations. The management command provides a callback that writes to stdout (unless in JSON mode).

**Tech Stack:** Python, Django management commands, existing LocalRecommendationProvider

---

## Example Output

```
$ python manage.py get_recommendations --disk --path=/var/log

Scanning /var/log for large files (>100 MB)...
  Checking /var/log/system.log (45 MB)
  Checking /var/log/install.log (12 MB)
  Found: /var/log/asl/ (234 MB) [LARGE]
  → Found 3 large items

Scanning for old files (>30 days)...
  Checking /var/log/monthly.out (45 days old)
  Found: /var/log/monthly.out (2 MB, 45 days) [OLD]
  → Found 5 old files

Found 2 recommendation(s):
...
```

In JSON mode (`--json`), no progress is shown - output is clean JSON only.

---

## Task 1: Add progress_callback parameter to LocalRecommendationProvider

**Files:**
- Modify: `apps/intelligence/providers/local.py`

**Step 1: Write the failing test**

```python
# apps/intelligence/tests.py

def test_provider_calls_progress_callback(self):
    """Provider should call progress_callback during operations."""
    progress_messages = []

    def capture_progress(msg):
        progress_messages.append(msg)

    provider = LocalRecommendationProvider(
        top_n_processes=3,
        progress_callback=capture_progress,
    )
    provider._get_memory_recommendations()

    assert len(progress_messages) > 0
    assert any("memory" in msg.lower() for msg in progress_messages)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/intelligence/tests.py::test_provider_calls_progress_callback -v`
Expected: FAIL (progress_callback not accepted)

**Step 3: Add progress_callback to __init__**

In `apps/intelligence/providers/local.py`, update `__init__`:

```python
def __init__(
    self,
    top_n_processes: int = 10,
    large_file_threshold_mb: float = 100.0,
    old_file_days: int = 30,
    scan_paths: list[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
):
    self.top_n_processes = top_n_processes
    self.large_file_threshold_mb = large_file_threshold_mb
    self.old_file_days = old_file_days
    self.scan_paths = scan_paths or ["/var/log", "/tmp", "/var/tmp"]
    self._progress = progress_callback or (lambda msg: None)  # no-op default
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/intelligence/tests.py::test_provider_calls_progress_callback -v`
Expected: Still FAIL (callback accepted but not called yet)

**Step 5: Commit partial progress**

```bash
git add apps/intelligence/providers/local.py
git commit -m "feat(intelligence): add progress_callback parameter to LocalRecommendationProvider"
```

---

## Task 2: Add progress calls to memory analysis

**Files:**
- Modify: `apps/intelligence/providers/local.py`

**Step 1: Write/update the test**

Test from Task 1 should now pass after this task.

**Step 2: Add progress calls to _get_memory_recommendations**

```python
def _get_memory_recommendations(self) -> list[Recommendation]:
    """Get memory-specific recommendations."""
    self._progress("Analyzing memory usage...")
    self._progress("  Collecting process information...")

    memory = psutil.virtual_memory()
    processes = []

    for proc in psutil.process_iter(["pid", "name", "memory_percent", "cmdline"]):
        try:
            info = proc.info
            if info["memory_percent"] and info["memory_percent"] > 0.1:
                processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    self._progress("  Sorting by memory usage...")
    processes.sort(key=lambda x: x["memory_percent"] or 0, reverse=True)
    top_processes = processes[: self.top_n_processes]

    if top_processes:
        top = top_processes[0]
        self._progress(f"  → Top consumer: {top['name']} ({top['memory_percent']:.1f}%)")

    self._progress(f"  → Found {len(top_processes)} processes using >0.1% memory")

    # ... rest of method unchanged
```

**Step 3: Run test**

Run: `uv run pytest apps/intelligence/tests.py::test_provider_calls_progress_callback -v`
Expected: PASS

**Step 4: Commit**

```bash
git add apps/intelligence/providers/local.py
git commit -m "feat(intelligence): add progress messages to memory analysis"
```

---

## Task 3: Add progress calls to disk analysis

**Files:**
- Modify: `apps/intelligence/providers/local.py`

**Step 1: Write the test**

```python
def test_provider_disk_progress_callback(self):
    """Provider should call progress_callback during disk scanning."""
    progress_messages = []

    def capture_progress(msg):
        progress_messages.append(msg)

    provider = LocalRecommendationProvider(
        large_file_threshold_mb=1000,  # High threshold to scan without finding much
        progress_callback=capture_progress,
    )
    provider._get_disk_recommendations("/tmp")

    assert any("Scanning" in msg for msg in progress_messages)
    assert any("/tmp" in msg for msg in progress_messages)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/intelligence/tests.py::test_provider_disk_progress_callback -v`
Expected: FAIL

**Step 3: Add progress calls to _get_disk_recommendations**

```python
def _get_disk_recommendations(self, path: str = "/") -> list[Recommendation]:
    """Get disk-specific recommendations."""
    self._progress(f"Scanning {path} for large files (>{self.large_file_threshold_mb} MB)...")

    large_items = []
    old_files = []
    threshold_bytes = self.large_file_threshold_mb * 1024 * 1024
    cutoff_time = time.time() - (self.old_file_days * 24 * 60 * 60)

    try:
        for entry in os.scandir(path):
            try:
                stat = entry.stat()
                size_mb = stat.st_size / (1024 * 1024)

                self._progress(f"  Checking {entry.path} ({size_mb:.1f} MB)")

                if stat.st_size > threshold_bytes:
                    self._progress(f"  Found: {entry.path} ({size_mb:.1f} MB) [LARGE]")
                    large_items.append({
                        "path": entry.path,
                        "size_mb": size_mb,
                        "is_directory": entry.is_dir(),
                    })

                if stat.st_mtime < cutoff_time and entry.is_file():
                    days_old = int((time.time() - stat.st_mtime) / (24 * 60 * 60))
                    self._progress(f"  Found: {entry.path} ({size_mb:.1f} MB, {days_old} days) [OLD]")
                    old_files.append({
                        "path": entry.path,
                        "size_mb": size_mb,
                        "days_old": days_old,
                    })
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass

    self._progress(f"  → Found {len(large_items)} large items")

    # ... rest of method
```

**Step 4: Run test**

Run: `uv run pytest apps/intelligence/tests.py::test_provider_disk_progress_callback -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/intelligence/providers/local.py
git commit -m "feat(intelligence): add progress messages to disk scanning"
```

---

## Task 4: Update get_provider factory to pass callback

**Files:**
- Modify: `apps/intelligence/providers/__init__.py`

**Step 1: Update get_provider signature**

```python
def get_provider(
    name: str = "local",
    progress_callback: Callable[[str], None] | None = None,
    **kwargs
) -> BaseProvider:
    """Get a provider instance by name."""
    if name not in PROVIDERS:
        raise KeyError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")

    provider_class = PROVIDERS[name]
    if name == "local":
        return provider_class(progress_callback=progress_callback, **kwargs)
    return provider_class(**kwargs)
```

**Step 2: Run existing tests**

Run: `uv run pytest apps/intelligence/tests.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add apps/intelligence/providers/__init__.py
git commit -m "feat(intelligence): pass progress_callback through get_provider factory"
```

---

## Task 5: Update management command to provide callback

**Files:**
- Modify: `apps/intelligence/management/commands/get_recommendations.py`

**Step 1: Write the test**

```python
# apps/intelligence/tests.py

from io import StringIO
from django.core.management import call_command

def test_get_recommendations_shows_progress(self):
    """Command should show progress messages in non-JSON mode."""
    out = StringIO()
    call_command("get_recommendations", "--memory", stdout=out)
    output = out.getvalue()

    assert "Analyzing memory" in output

def test_get_recommendations_no_progress_in_json_mode(self):
    """Command should NOT show progress messages in JSON mode."""
    out = StringIO()
    call_command("get_recommendations", "--memory", "--json", stdout=out)
    output = out.getvalue()

    # Should be valid JSON with no progress text mixed in
    import json
    data = json.loads(output)
    assert "provider" in data
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/intelligence/tests.py::test_get_recommendations_shows_progress -v`
Expected: FAIL

**Step 3: Update command to use progress callback**

```python
class Command(BaseCommand):
    help = "Get intelligence recommendations based on system state or incidents"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_mode = False

    def _progress(self, msg: str) -> None:
        """Write progress message (only in non-JSON mode)."""
        if not self._json_mode:
            self.stdout.write(msg)

    def handle(self, *args, **options):
        self._json_mode = options.get("json", False)

        # ... list_providers handling unchanged ...

        # Get provider with progress callback
        try:
            provider = get_provider(
                options["provider"],
                top_n_processes=options["top_n"],
                large_file_threshold_mb=options["threshold_mb"],
                old_file_days=options["old_days"],
                progress_callback=self._progress,
            )
        except KeyError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return

        # ... rest unchanged ...
```

**Step 4: Run tests**

Run: `uv run pytest apps/intelligence/tests.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/intelligence/management/commands/get_recommendations.py
git commit -m "feat(intelligence): show progress messages in get_recommendations command"
```

---

## Task 6: Update tests and verify full integration

**Files:**
- Modify: `apps/intelligence/tests.py`

**Step 1: Run full test suite**

Run: `uv run pytest apps/intelligence/tests.py -v`
Expected: All PASS

**Step 2: Manual verification**

```bash
# Should show progress
uv run python manage.py get_recommendations --memory

# Should be silent (clean JSON)
uv run python manage.py get_recommendations --memory --json

# Should show disk scanning progress
uv run python manage.py get_recommendations --disk --path=/tmp
```

**Step 3: Final commit**

```bash
git add -A
git commit -m "test(intelligence): add tests for progress callback functionality"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Add `progress_callback` parameter to `LocalRecommendationProvider` |
| 2 | Add progress calls to memory analysis |
| 3 | Add progress calls to disk analysis |
| 4 | Update `get_provider` factory to pass callback |
| 5 | Update management command to provide callback |
| 6 | Integration tests and verification |
