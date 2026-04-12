---
title: "Path Traversal Prevention Design"
parent: Plans
---

# Path Traversal Prevention

## Problem

Multiple entry points across the codebase accept user-supplied file/directory paths without validation, enabling path traversal attacks. An attacker could read arbitrary files, enumerate directories, or trigger resource-intensive scans on sensitive locations.

## Audit Findings

| Caller | Entry Method | Current Validation | Risk |
|--------|-------------|-------------------|------|
| `intelligence/views/disk.py` | `request.GET` | Inline allowlist (already fixed) | LOW |
| `intelligence/providers/local.py` | Constructor param → `subprocess du` | None | HIGH |
| `intelligence/management/commands/get_recommendations.py` | `--path` CLI arg | None | HIGH |
| `orchestration/management/commands/run_pipeline.py` | `--file`, `--config` CLI args | None | HIGH |
| `checkers/management/commands/check_health.py` | `--disk-paths` CLI arg | None | HIGH |
| `checkers/management/commands/run_check.py` | `--paths` CLI arg | None | HIGH |
| `notify/templating.py` | DB config / template name | None | HIGH |

## Solution: Centralized Security Package

### Package Structure

```
config/security/
  __init__.py              # Re-exports all public APIs
  path_traversal.py        # Path traversal prevention
  (future modules)         # e.g., injection.py, secret_redaction.py
```

Grouped by attack/protection type so future security checks slot in naturally.

### `path_traversal.py` API

```python
class PathNotAllowedError(ValueError):
    """Raised when a path fails traversal validation."""

ALLOWED_FILESYSTEM_ROOTS: tuple[str, ...]
# Resolved at import: ("/", "/var", "/tmp", "/home", "/opt", "/srv", "/usr")

def resolve_safe_path(
    user_input: str,
    allowed_roots: tuple[str, ...] = ALLOWED_FILESYSTEM_ROOTS,
) -> str:
    """Resolve to absolute path and validate against allowlist.
    Raises PathNotAllowedError if outside allowed roots."""

def resolve_safe_name(name: str) -> str:
    """Validate a filename — no slashes, no .., no leading dots.
    Raises PathNotAllowedError if name contains traversal characters."""
```

### Caller Integration

| Caller | Function | Error Handling |
|--------|----------|---------------|
| `intelligence/views/disk.py` | `resolve_safe_path()` | Catch → 400 JSON |
| `intelligence/providers/local.py` | `resolve_safe_path()` | Let propagate |
| `intelligence/commands/get_recommendations.py` | `resolve_safe_path()` | Catch → `CommandError` |
| `orchestration/commands/run_pipeline.py` | `resolve_safe_path()` | Catch → `CommandError` |
| `checkers/commands/check_health.py` | `resolve_safe_path()` | Catch → `CommandError` |
| `checkers/commands/run_check.py` | `resolve_safe_path()` | Catch → `CommandError` |
| `notify/templating.py` | `resolve_safe_name()` | Return `None` (no template found) |

### Error Handling Pattern

- **HTTP views**: catch `PathNotAllowedError` → return 400 with error message
- **Management commands**: catch `PathNotAllowedError` → raise `CommandError`
- **Internal callers** (templating, providers): let it propagate to the caller above

## What This Does NOT Cover (Next Phase)

- Security-specific test suite for all entry points
- Additional centralized checks (injection, secret redaction)
- Django system check to detect unprotected path usage

## Documentation Updates

- `docs/Security.md` — Path Traversal Protection section (already added)
- `CLAUDE.md`, `agents.md`, `copilot-instructions.md` — absolute path rule (already added)
- App-level `agents.md` for intelligence and checkers (already added)