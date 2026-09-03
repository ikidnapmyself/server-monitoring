# Container PaaS deployment

`Dockerfile` in this directory is the deployment interface for container platforms that
build from a repository — Coolify, Dokku, CapRover, Render, Fly.io, Railway.

It is **not** the recommended way to run this project on a machine you control. For that,
see `deploy/nginx/` + `deploy/systemd/` (self-managed VPS) or `deploy/forge/` (managed
VPS panel).

## What the image does

Multi-stage build: dependencies resolve in a builder stage via `uv sync --frozen --no-dev
--extra prod`, then the venv is copied into a slim runtime stage. Runs as a non-root
`appuser`. On start it runs `collectstatic`, then `migrate`, then gunicorn.

The Python base is pinned to `3.10-slim` to match `.python-version` and `requires-python`
in `pyproject.toml`. **If you bump the project's Python version, bump both `FROM` lines
here too** — a mismatch means CI and production run different interpreters.

## Required environment variables

| Variable | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | Required. Startup runs `collectstatic`, which loads settings. |
| `DJANGO_ALLOWED_HOSTS` | Your platform hostname, comma-separated. |
| `DJANGO_DEBUG` | Set `0`. |
| `DJANGO_ENV` | Set `prod`. |
| `PORT` | Injected by most platforms. Defaults to `8000` if unset. |
| `WEB_CONCURRENCY` | Gunicorn workers. Defaults to `3`. |
| `DATABASE_PATH` | Defaults to `/app/data/db.sqlite3`. See the persistence warning below. |

## Persistence warning — read this first

The project uses **SQLite** (`config/settings.py`), stored at `/app/data/db.sqlite3`.
Container filesystems are ephemeral: without a mounted volume at `/app/data`, **your
database is destroyed on every deploy and every restart.**

Every platform below needs a persistent volume mounted at `/app/data`:

- **Coolify / Dokku / CapRover** — add a persistent volume or bind mount for `/app/data`.
- **Fly.io** — create a volume and mount it at `/app/data` in `fly.toml`.
- **Render** — attach a Persistent Disk at `/app/data`.
- **Railway** — attach a volume at `/app/data`.

Also note SQLite does not tolerate multiple instances writing the same file across
containers. **Run a single web instance**, or migrate to Postgres before scaling out.

## Health check

Point the platform's health check at **`/intelligence/health/`** (`apps/intelligence/urls.py`
mounted under the `intelligence/` prefix in `config/urls.py` — there is no top-level
`/health/`). Allow at least 30s of startup grace: `collectstatic` and `migrate` both run
before gunicorn binds.
