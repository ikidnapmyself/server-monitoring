"""Env file consistency checks.

Compares .env, .env.sample, and config/settings.py to detect drift:
- Keys in .env.sample missing from .env
- Keys in .env not in .env.sample
- Keys referenced in settings.py missing from .env.sample
- Keys in .env.sample never referenced in settings.py
"""

import re
from pathlib import Path

from apps.checkers.status import CheckResult

CATEGORY = "env"


def _read_file(path: Path) -> str | None:
    """Read file contents or return None if missing."""
    try:
        return path.read_text()
    except (FileNotFoundError, PermissionError):
        return None


def parse_env_keys(content: str) -> set[str]:
    """Extract variable names from .env file content."""
    keys = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


def parse_sample_keys(content: str) -> tuple[set[str], set[str]]:
    """Extract active and commented-out keys from .env.sample.

    Returns:
        (active_keys, commented_keys)
    """
    active = set()
    commented = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key:
                active.add(key)
        elif stripped.startswith("#") and not stripped.startswith("##"):
            rest = stripped.lstrip("# ")
            if "=" in rest:
                key = rest.split("=", 1)[0].strip()
                if re.match(r"^[A-Z][A-Z0-9_]*$", key):
                    commented.add(key)
    return active, commented


def parse_settings_env_refs(content: str) -> set[str]:
    """Extract env var names from os.environ.get() calls in settings.py."""
    return set(re.findall(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']', content))


def run(base_dir: Path) -> list[CheckResult]:
    """Run all env file consistency checks."""
    results: list[CheckResult] = []

    env_content = _read_file(base_dir / ".env")
    if env_content is None:
        results.append(
            CheckResult(
                level="error",
                message=".env file not found",
                hint="Copy .env.sample to .env and configure it.",
                category=CATEGORY,
            )
        )
        return results

    sample_content = _read_file(base_dir / ".env.sample")
    if sample_content is None:
        results.append(
            CheckResult(
                level="warn",
                message=".env.sample not found",
                hint=".env.sample serves as the reference for expected env vars.",
                category=CATEGORY,
            )
        )
        return results

    settings_content = _read_file(base_dir / "config" / "settings.py")

    env_keys = parse_env_keys(env_content)
    sample_active, sample_commented = parse_sample_keys(sample_content)
    all_sample_keys = sample_active | sample_commented
    settings_refs = parse_settings_env_refs(settings_content) if settings_content else set()

    missing_from_env = sample_active - env_keys
    for key in sorted(missing_from_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is in .env.sample but missing from .env",
                hint="Add it to .env or remove from .env.sample if no longer needed.",
                category=CATEGORY,
            )
        )

    unknown_in_env = env_keys - all_sample_keys
    for key in sorted(unknown_in_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is in .env but not documented in .env.sample",
                hint="Add it to .env.sample so others know about it.",
                category=CATEGORY,
            )
        )

    undocumented = settings_refs - all_sample_keys
    for key in sorted(undocumented):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is referenced in settings.py but missing from .env.sample",
                hint="Document it in .env.sample.",
                category=CATEGORY,
            )
        )

    if settings_refs:
        unreferenced = sample_active - settings_refs
        for key in sorted(unreferenced):
            results.append(
                CheckResult(
                    level="warn",
                    message=f"{key} is in .env.sample but never referenced in settings.py",
                    hint=(
                        "Remove from .env.sample if no longer used, "
                        "or it may be used in shell scripts only."
                    ),
                    category=CATEGORY,
                )
            )

    if not results:
        results.append(
            CheckResult(level="ok", message="All .env keys are consistent", category=CATEGORY)
        )

    return results
