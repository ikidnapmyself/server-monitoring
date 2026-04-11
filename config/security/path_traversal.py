from pathlib import Path

ALLOWED_FILESYSTEM_ROOTS = tuple(
    str(Path(p).resolve()) for p in ("/", "/var", "/tmp", "/home", "/opt", "/srv", "/usr")
)


class PathNotAllowedError(ValueError):
    """Raised when a path fails traversal validation."""


def resolve_safe_path(
    user_input: str,
    allowed_roots: tuple[str, ...] = ALLOWED_FILESYSTEM_ROOTS,
) -> str:
    """Resolve to absolute and validate against allowlist. Raises PathNotAllowedError."""
    resolved = str(Path(user_input).resolve())
    if any(resolved == root or resolved.startswith(root + "/") for root in allowed_roots):
        return resolved
    raise PathNotAllowedError(
        f"Path not allowed: {user_input!r} (resolved to {resolved!r}). "
        f"Must be under one of: {', '.join(allowed_roots)}"
    )


def resolve_safe_name(name: str) -> str:
    """Validate filename -- no slashes, backslashes, leading dots, or '..' sequences.

    Raises PathNotAllowedError.
    """
    if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        raise PathNotAllowedError(
            f"Filename not allowed: {name!r}. "
            "Must not contain slashes, backslashes, leading dots, or '..' sequences."
        )
    return name
