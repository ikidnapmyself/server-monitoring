---
title: "Path Traversal Prevention Implementation Plan"
parent: Plans
---

# Path Traversal Prevention Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Centralize path traversal prevention into `config/security/` and wire all vulnerable entry points to use it.

**Architecture:** A `config/security/` package grouped by attack type. The `path_traversal` module provides `resolve_safe_path()` for filesystem paths and `resolve_safe_name()` for filenames/template names. Every caller that accepts user-supplied paths imports from this module.

**Tech Stack:** Python 3.10+, pathlib, Django management commands, pytest

---

### Task 1: Create `config/security/` package with `path_traversal` module

**Files:**
- Create: `config/security/__init__.py`
- Create: `config/security/path_traversal.py`
- Test: `config/_tests/security/test_path_traversal.py`

**Step 1: Write the failing tests**

Create `config/_tests/security/__init__.py` (empty) and `config/_tests/security/test_path_traversal.py`:

```python
"""Tests for config.security.path_traversal."""

from pathlib import Path

import pytest

from config.security.path_traversal import (
    ALLOWED_FILESYSTEM_ROOTS,
    PathNotAllowedError,
    resolve_safe_name,
    resolve_safe_path,
)


class TestResolveSafePath:
    """Tests for resolve_safe_path()."""

    def test_absolute_path_within_allowed_root(self):
        result = resolve_safe_path("/var/log", ALLOWED_FILESYSTEM_ROOTS)
        assert result == str(Path("/var/log").resolve())

    def test_root_path_allowed(self):
        result = resolve_safe_path("/", ALLOWED_FILESYSTEM_ROOTS)
        assert result == str(Path("/").resolve())

    def test_traversal_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("/../../../etc/shadow", ALLOWED_FILESYSTEM_ROOTS)

    def test_disallowed_path_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("/root/.ssh", ALLOWED_FILESYSTEM_ROOTS)

    def test_relative_path_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("relative/path", ("/usr",))

    def test_custom_allowed_roots(self):
        custom = (str(Path("/tmp").resolve()),)
        result = resolve_safe_path("/tmp/myfile.json", custom)
        assert result == str(Path("/tmp/myfile.json").resolve())

    def test_custom_root_rejects_outside(self):
        custom = (str(Path("/tmp").resolve()),)
        with pytest.raises(PathNotAllowedError):
            resolve_safe_path("/var/log", custom)

    def test_default_roots_are_resolved(self):
        """Allowed roots should be resolved (handles macOS /tmp -> /private/tmp)."""
        for root in ALLOWED_FILESYSTEM_ROOTS:
            assert root == str(Path(root).resolve())


class TestResolveSafeName:
    """Tests for resolve_safe_name()."""

    def test_simple_name_allowed(self):
        assert resolve_safe_name("slack_text.j2") == "slack_text.j2"

    def test_name_with_hyphen_allowed(self):
        assert resolve_safe_name("my-template.j2") == "my-template.j2"

    def test_traversal_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("../../../etc/passwd")

    def test_slash_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("subdir/template.j2")

    def test_leading_dot_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name(".hidden")

    def test_backslash_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("sub\\template.j2")

    def test_empty_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest config/_tests/security/test_path_traversal.py -v`
Expected: FAIL with ImportError

**Step 3: Write the implementation**

Create `config/security/__init__.py`:

```python
"""Centralized security utilities grouped by attack/protection type."""

from config.security.path_traversal import (
    ALLOWED_FILESYSTEM_ROOTS,
    PathNotAllowedError,
    resolve_safe_name,
    resolve_safe_path,
)

__all__ = [
    "ALLOWED_FILESYSTEM_ROOTS",
    "PathNotAllowedError",
    "resolve_safe_name",
    "resolve_safe_path",
]
```

Create `config/security/path_traversal.py`:

```python
"""Path traversal prevention utilities.

Provides functions to validate user-supplied filesystem paths and filenames
against allowlists, preventing directory traversal attacks.
"""

from pathlib import Path

ALLOWED_FILESYSTEM_ROOTS = tuple(
    str(Path(p).resolve())
    for p in ("/", "/var", "/tmp", "/home", "/opt", "/srv", "/usr")
)


class PathNotAllowedError(ValueError):
    """Raised when a path fails traversal validation."""


def resolve_safe_path(
    user_input: str,
    allowed_roots: tuple[str, ...] = ALLOWED_FILESYSTEM_ROOTS,
) -> str:
    """Resolve a user-supplied path to absolute form and validate against an allowlist.

    Args:
        user_input: Raw path string from user input.
        allowed_roots: Tuple of resolved absolute paths that are permitted.

    Returns:
        The resolved absolute path string.

    Raises:
        PathNotAllowedError: If the resolved path is outside all allowed roots.
    """
    resolved = str(Path(user_input).resolve())
    if not any(
        resolved == root or resolved.startswith(root + "/")
        for root in allowed_roots
    ):
        return resolved  # This line won't be reached due to the raise below
    # Check passes — but we need to invert the logic:
    for root in allowed_roots:
        if resolved == root or resolved.startswith(root + "/"):
            return resolved
    raise PathNotAllowedError(
        f"Path not allowed: {user_input!r} (resolved to {resolved!r}). "
        f"Must be under one of: {', '.join(allowed_roots)}"
    )


def resolve_safe_name(name: str) -> str:
    """Validate a filename or template name contains no traversal characters.

    Rejects names containing slashes, backslashes, leading dots, or '..' sequences.

    Args:
        name: The filename to validate.

    Returns:
        The validated name (unchanged).

    Raises:
        PathNotAllowedError: If the name contains traversal characters.
    """
    if (
        not name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or ".." in name
    ):
        raise PathNotAllowedError(
            f"Filename not allowed: {name!r}. "
            "Must not contain slashes, backslashes, leading dots, or '..' sequences."
        )
    return name
```

Wait — the `resolve_safe_path` function above has a logic bug. Here is the correct version:

```python
def resolve_safe_path(
    user_input: str,
    allowed_roots: tuple[str, ...] = ALLOWED_FILESYSTEM_ROOTS,
) -> str:
    resolved = str(Path(user_input).resolve())
    if any(
        resolved == root or resolved.startswith(root + "/")
        for root in allowed_roots
    ):
        return resolved
    raise PathNotAllowedError(
        f"Path not allowed: {user_input!r} (resolved to {resolved!r}). "
        f"Must be under one of: {', '.join(allowed_roots)}"
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest config/_tests/security/test_path_traversal.py -v`
Expected: All PASS

**Step 5: Check coverage**

Run: `uv run coverage run -m pytest config/_tests/security/test_path_traversal.py && uv run coverage report --include="config/security/*"`
Expected: 100% branch coverage

**Step 6: Commit**

```bash
git add config/security/ config/_tests/security/
git commit -m "feat(security): add centralized path traversal prevention utilities"
```

---

### Task 2: Wire `intelligence/views/disk.py` to use centralized utility

**Files:**
- Modify: `apps/intelligence/views/disk.py`
- Modify: `apps/intelligence/_tests/views/test_disk.py`

**Step 1: Update the view**

Replace the inline `ALLOWED_ANALYSIS_ROOTS` and manual validation with:

```python
from config.security import PathNotAllowedError, resolve_safe_path
```

Remove the `ALLOWED_ANALYSIS_ROOTS` constant and the inline validation block. Replace with:

```python
try:
    path = resolve_safe_path(request.GET.get("path", "/"))
except PathNotAllowedError as e:
    return self.error_response(str(e), status=400)
```

**Step 2: Update tests**

Update `test_get_disk_analysis_path_traversal_rejected` and `test_get_disk_analysis_disallowed_path_rejected` to match on the new error message format (should still contain "not allowed").

**Step 3: Run tests**

Run: `uv run pytest apps/intelligence/_tests/views/test_disk.py -v`
Expected: All PASS

**Step 4: Check coverage**

Run: `uv run coverage run -m pytest apps/intelligence/_tests/views/test_disk.py && uv run coverage report --include="apps/intelligence/views/disk.py"`
Expected: 100%

**Step 5: Commit**

```bash
git add apps/intelligence/views/disk.py apps/intelligence/_tests/views/test_disk.py
git commit -m "refactor(intelligence): use centralized path validation in disk view"
```

---

### Task 3: Wire `intelligence/providers/local.py` to validate before subprocess

**Files:**
- Modify: `apps/intelligence/providers/local.py` — `_scan_large_files()` and `_find_old_logs()`

**Step 1: Add validation at the top of `_scan_large_files()` and `_find_old_logs()`**

Import `resolve_safe_path` and call it on `root_path` / `path` before use:

```python
from config.security import resolve_safe_path

# At top of _scan_large_files:
root_path = resolve_safe_path(root_path)

# At top of _find_old_logs, when path != "/":
if path != "/":
    path = resolve_safe_path(path)
```

For `path == "/"` the default scan_dirs are hardcoded, so no validation needed.

**Step 2: Run existing tests**

Run: `uv run pytest apps/intelligence/_tests/providers/test_local.py -v`
Expected: All PASS (existing tests use allowed paths like `/` or mock subprocess)

**Step 3: Commit**

```bash
git add apps/intelligence/providers/local.py
git commit -m "fix(intelligence): validate paths before subprocess in local provider"
```

---

### Task 4: Wire `notify/templating.py` to use `resolve_safe_name()`

**Files:**
- Modify: `apps/notify/templating.py` — `_load_template_from_file()`

**Step 1: Add name validation**

```python
from config.security import PathNotAllowedError, resolve_safe_name

def _load_template_from_file(name: str) -> str | None:
    try:
        name = resolve_safe_name(name)
    except PathNotAllowedError:
        return None
    path = TEMPLATES_DIR / name
    # ... rest unchanged
```

**Step 2: Run existing tests**

Run: `uv run pytest apps/notify/_tests/ -v -k template`
Expected: All PASS

**Step 3: Commit**

```bash
git add apps/notify/templating.py
git commit -m "fix(notify): validate template names against path traversal"
```

---

### Task 5: Wire management commands — `get_recommendations`, `run_pipeline`, `check_health`, `run_check`

**Files:**
- Modify: `apps/intelligence/management/commands/get_recommendations.py`
- Modify: `apps/orchestration/management/commands/run_pipeline.py`
- Modify: `apps/checkers/management/commands/check_health.py`
- Modify: `apps/checkers/management/commands/run_check.py`

**Step 1: Add validation to each command's `handle()` method**

Pattern for all commands — import and validate early:

```python
from config.security import PathNotAllowedError, resolve_safe_path
```

**get_recommendations.py** — before `provider.run(analysis_type="disk", path=...)`:
```python
try:
    validated_path = resolve_safe_path(options["path"])
except PathNotAllowedError as e:
    raise CommandError(str(e))
# Then use validated_path instead of options["path"]
```

**run_pipeline.py** — for `--file` and `--config`:
```python
try:
    file_path = resolve_safe_path(options["file"])
except PathNotAllowedError as e:
    raise CommandError(str(e))
with open(file_path) as f:
    ...
```

Same pattern for `config_path`.

**check_health.py** — for `--disk-paths`:
```python
if name == "disk" and options["disk_paths"]:
    try:
        kwargs["paths"] = [resolve_safe_path(p) for p in options["disk_paths"]]
    except PathNotAllowedError as e:
        raise CommandError(str(e))
```

**run_check.py** — for `--paths`:
```python
if options.get("paths"):
    try:
        kwargs["paths"] = [resolve_safe_path(p) for p in options["paths"]]
    except PathNotAllowedError as e:
        raise CommandError(str(e))
```

**Step 2: Run all tests**

Run: `uv run pytest apps/intelligence/_tests/management/ apps/orchestration/_tests/management/ apps/checkers/_tests/management/ -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add apps/intelligence/management/ apps/orchestration/management/ apps/checkers/management/
git commit -m "fix(security): validate paths in all management commands"
```

---

### Task 6: Update `docs/Security.md` with centralized utility reference

**Files:**
- Modify: `docs/Security.md`

**Step 1: Update the Path Traversal Protection section**

Update the reference implementation to point to `config/security/path_traversal.py` instead of the inline code in `disk.py`. Add usage examples for both `resolve_safe_path()` and `resolve_safe_name()`.

**Step 2: Commit**

```bash
git add docs/Security.md
git commit -m "docs: update security docs with centralized path validation utility"
```

---

### Task 7: Full test suite and coverage verification

**Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: All PASS

**Step 2: Check coverage on security module**

Run: `uv run coverage run -m pytest && uv run coverage report --include="config/security/*"`
Expected: 100% branch coverage

**Step 3: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: All PASS