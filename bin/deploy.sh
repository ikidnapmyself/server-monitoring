#!/usr/bin/env bash

#set -euo pipefail

## If uv is installed in a user-local location, add it to PATH.
#export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

dotenv_ensure_file() {
  local env_file=".env"
  local sample_file=".env.sample"

  if [ -f "$env_file" ]; then
    return 0
  fi

  if [ -f "$sample_file" ]; then
    cp "$sample_file" "$env_file"
    return 0
  fi

  touch "$env_file"
}

dotenv_has_key() {
  local file="$1"
  local key="$2"
  grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$file"
}

dotenv_set_if_missing() {
  local file="$1"
  local key="$2"
  local value="$3"

  if dotenv_has_key "$file" "$key"; then
    return 0
  fi

  printf "%s=%s\n" "$key" "$value" >>"$file"
}

dotenv_ensure_secret_key() {
  local env_file="$1"

  # If it's already present in .env, we're done.
  if dotenv_has_key "$env_file" "DJANGO_SECRET_KEY"; then
    return 0
  fi

  # If it's already present in process env (Forge Environment), don't duplicate it.
  if [ -n "${DJANGO_SECRET_KEY:-}" ]; then
    return 0
  fi

  if command_exists python3; then
    local key
    key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
    dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$key"
    return 0
  fi

  echo "[ERROR] DJANGO_SECRET_KEY is not set and python3 is not available to generate one." >&2
  echo "        Set DJANGO_SECRET_KEY in Forge Environment or in .env." >&2
  exit 1
}

# Optional: activate a per-release virtualenv if your server is configured that way.
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# ---------------------------------------------------------------------------
# .env (non-interactive)
# ---------------------------------------------------------------------------

dotenv_ensure_file

# Best-effort prod defaults. These won't overwrite anything existing.
# (config/settings.py defaults DEBUG to "1" if unset, so set it explicitly.)
dotenv_set_if_missing ".env" "DJANGO_ENV" "prod"
dotenv_set_if_missing ".env" "DJANGO_DEBUG" "0"
dotenv_set_if_missing ".env" "CELERY_TASK_ALWAYS_EAGER" "0"

# Ensure SECRET_KEY exists somewhere (Forge env or .env)
dotenv_ensure_secret_key ".env"

# ---------------------------------------------------------------------------
# Dependencies + Django steps
# ---------------------------------------------------------------------------

uv sync

uv run python manage.py migrate --noinput
uv run python manage.py check

