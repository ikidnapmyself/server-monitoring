"""Environment variable loading helpers.

This project supports local configuration via dotenv-style files.

Load order (first found wins; existing process env vars are never overridden):
- .env
- .env.dev (only when DJANGO_ENV=dev)

In production, prefer real environment variables instead of dotenv files.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _should_load_dev_env() -> bool:
    # You can opt into .env.dev by setting DJANGO_ENV=dev.
    return os.environ.get("DJANGO_ENV", "").lower() in {"dev", "development", "local"}


def load_env(base_dir: Path | None = None) -> None:
    """Load .env files into process environment.

    Safe to call multiple times.

    Args:
        base_dir: Project root directory. Defaults to the Django BASE_DIR
            (config/..), following the same convention as config/settings.py.
    """

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent

    env_path = base_dir / ".env"
    load_dotenv(env_path, override=False)

    if _should_load_dev_env():
        dev_env_path = base_dir / ".env.dev"
        load_dotenv(dev_env_path, override=False)
