# Spinner Progress for `get_recommendations` Command

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace verbose multi-line progress output with a single-line spinner that updates in-place, while still printing discoveries as they're found.

**Architecture:** Add a `SpinnerProgress` helper class that handles terminal output with carriage return (`\r`) for in-place updates. The existing `progress_callback` will use this helper instead of simple `stdout.write()`.

**Tech Stack:** Python, ANSI terminal codes, no new dependencies

---

## Example Output

**Before (current):**
```
Scanning /var/log for large files (>100.0 MB)...
  Checking /var/log/system.log (45 files scanned)
  Checking /var/log/install.log (90 files scanned)
  Found: /var/log/archive.tar.gz (234 MB) [LARGE]
  -> Found 1 large items
```

**After (spinner):**
```
⠋ Scanning /var/log... 45 files
⠙ Scanning /var/log... 90 files
  Found: /var/log/archive.tar.gz (234 MB) [LARGE]
⠹ Scanning /var/log... 135 files
✓ Scanned 135 files, found 1 large items
```

The spinner line overwrites itself. Discoveries print on new lines then spinner resumes.

---

## Task 1: Create SpinnerProgress helper class

**Files:**
- Create: `apps/intelligence/utils/spinner.py`

**Step 1: Write the test**

```python
# apps/intelligence/_tests/utils/test_spinner.py
import io
from apps.intelligence.utils.spinner import SpinnerProgress

def test_spinner_update_overwrites_line():
    """Spinner update should use carriage return to overwrite."""
    output = io.StringIO()
    spinner = SpinnerProgress(output, is_tty=True)
    spinner.update("Processing... 10 files")
    spinner.update("Processing... 20 files")
    # Output should contain \r for overwriting
    assert "\r" in output.getvalue()

def test_spinner_found_prints_on_new_line():
    """Found items should print on their own line."""
    output = io.StringIO()
    spinner = SpinnerProgress(output, is_tty=True)
    spinner.update("Scanning...")
    spinner.found("Found: /path/file.log (100 MB)")
    assert "Found: /path/file.log" in output.getvalue()
    assert "\n" in output.getvalue()

def test_spinner_finish_shows_checkmark():
    """Finish should show completion with checkmark."""
    output = io.StringIO()
    spinner = SpinnerProgress(output, is_tty=True)
    spinner.finish("Done, found 3 items")
    assert "✓" in output.getvalue()

def test_spinner_non_tty_fallback():
    """Non-TTY should fall back to simple output without \r."""
    output = io.StringIO()
    spinner = SpinnerProgress(output, is_tty=False)
    spinner.update("Processing...")
    spinner.update("Processing...")
    # Should not use \r, just append
    assert output.getvalue().count("\r") == 0
```

**Step 2: Implement SpinnerProgress**

```python
# apps/intelligence/utils/spinner.py
"""Terminal spinner for progress indication."""

import sys
from typing import TextIO

SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class SpinnerProgress:
    """Single-line spinner that updates in-place.

    Usage:
        spinner = SpinnerProgress(sys.stdout)
        spinner.update("Scanning... 10 files")
        spinner.update("Scanning... 20 files")  # Overwrites previous
        spinner.found("Found: /path/file (100 MB)")  # Prints on new line
        spinner.finish("Done")  # Final line with checkmark
    """

    def __init__(self, output: TextIO | None = None, is_tty: bool | None = None):
        self.output = output or sys.stdout
        self.is_tty = is_tty if is_tty is not None else getattr(self.output, 'isatty', lambda: False)()
        self._spinner_idx = 0
        self._last_line_len = 0

    def _get_spinner_char(self) -> str:
        """Get next spinner character."""
        char = SPINNER_CHARS[self._spinner_idx % len(SPINNER_CHARS)]
        self._spinner_idx += 1
        return char

    def _clear_line(self) -> None:
        """Clear the current line."""
        if self.is_tty and self._last_line_len > 0:
            # Move to start of line and clear with spaces
            self.output.write("\r" + " " * self._last_line_len + "\r")

    def update(self, message: str) -> None:
        """Update spinner with new message (overwrites previous line)."""
        if self.is_tty:
            self._clear_line()
            line = f"{self._get_spinner_char()} {message}"
            self.output.write(line)
            self.output.flush()
            self._last_line_len = len(line)
        # Non-TTY: don't spam updates, only show significant messages

    def found(self, message: str) -> None:
        """Print a discovery on its own line, then resume spinner position."""
        if self.is_tty:
            self._clear_line()
        self.output.write(f"  {message}\n")
        self.output.flush()
        self._last_line_len = 0

    def finish(self, message: str) -> None:
        """Print final completion message with checkmark."""
        if self.is_tty:
            self._clear_line()
        self.output.write(f"✓ {message}\n")
        self.output.flush()
        self._last_line_len = 0

    def start(self, message: str) -> None:
        """Print starting message (for non-TTY or initial state)."""
        if not self.is_tty:
            self.output.write(f"{message}\n")
            self.output.flush()
        else:
            self.update(message)
```

**Step 3: Run test**

Run: `uv run pytest apps/intelligence/_tests/utils/test_spinner.py -v`

**Step 4: Commit**

```bash
git add apps/intelligence/utils/
git commit -m "feat(intelligence): add SpinnerProgress helper for terminal progress"
```

---

## Task 2: Update progress callback in management command to use spinner

**Files:**
- Modify: `apps/intelligence/management/commands/get_recommendations.py`

**Step 1: Update the command to use SpinnerProgress**

```python
# In get_recommendations.py

from apps.intelligence.utils.spinner import SpinnerProgress

class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_mode = False
        self._spinner: SpinnerProgress | None = None

    def _progress(self, msg: str) -> None:
        """Progress callback - routes to spinner or silent based on mode."""
        if self._json_mode or self._spinner is None:
            return

        # Route different message types
        if msg.startswith("  Found:"):
            self._spinner.found(msg.strip())
        elif msg.startswith("✓") or msg.startswith("->") or msg.startswith("→"):
            self._spinner.finish(msg.strip())
        else:
            self._spinner.update(msg.strip())

    def handle(self, *args, **options):
        self._json_mode = options.get("json", False)

        if not self._json_mode:
            self._spinner = SpinnerProgress(self.stdout)

        # ... rest of handle unchanged
```

**Step 2: Update tests**

```python
# Update test_get_recommendations_shows_progress
def test_get_recommendations_shows_progress(self):
    out = StringIO()
    # Simulate TTY for spinner behavior
    out.isatty = lambda: False  # Non-TTY fallback
    call_command("get_recommendations", "--memory", stdout=out)
    output = out.getvalue()
    # Should still show completion message
    assert "✓" in output or "Analyzing" in output
```

**Step 3: Run tests**

Run: `uv run pytest apps/intelligence/ -v`

**Step 4: Commit**

```bash
git add apps/intelligence/management/commands/get_recommendations.py
git commit -m "feat(intelligence): use spinner progress in get_recommendations command"
```

---

## Task 3: Update provider progress calls to emit spinner-compatible messages

**Files:**
- Modify: `apps/intelligence/providers/local.py`

**Step 1: Simplify progress messages for spinner**

Update `_get_disk_recommendations` and `_scan_large_files` to emit:
- `"Scanning {path}... {n} files"` for updates (no indentation)
- `"Found: {path} ({size} MB) [LARGE]"` for discoveries (will be indented by spinner)
- `"Scanned {n} files, found {m} large items"` for completion

**Step 2: Run tests**

Run: `uv run pytest apps/intelligence/ -v`

**Step 3: Verify manually**

```bash
uv run python manage.py get_recommendations --disk --path=/tmp
```

Should show spinner updating in place with discoveries printing on their own lines.

**Step 4: Commit**

```bash
git add apps/intelligence/providers/local.py
git commit -m "feat(intelligence): update progress messages for spinner format"
```

---

## Task 4: Integration testing

**Step 1: Run full test suite**

```bash
uv run pytest apps/intelligence/ -v
```

**Step 2: Manual verification**

```bash
# Memory analysis with spinner
uv run python manage.py get_recommendations --memory

# Disk analysis with spinner
uv run python manage.py get_recommendations --disk --path=/tmp

# JSON mode (should be silent)
uv run python manage.py get_recommendations --memory --json
```

**Step 3: Final commit if needed**

```bash
git add -A
git commit -m "test(intelligence): verify spinner progress integration"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Create `SpinnerProgress` helper class |
| 2 | Update management command to use spinner |
| 3 | Update provider progress messages for spinner format |
| 4 | Integration testing |
