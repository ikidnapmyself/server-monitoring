# server-maintanence

A small, modular Django project for basic **server maintenance / monitoring** workflows.

This top-level README is the entry point and documentation hub. App-specific docs live alongside the apps.

## Documentation map

- Health checks (checkers): [`apps/checkers/README.md`](apps/checkers/README.md)
- Working with repo AI agents / conventions: [`agents.md`](agents.md)

## Requirements

- Python **3.10+**
- Package manager: **uv** (recommended, repo includes `uv.lock`)

Dependencies (from `pyproject.toml`): Django + psutil.

## Install

### Quick Install (recommended)

Run the installer script which handles everything automatically:

```bash
./bin/install.sh
```

This will:
- Check Python 3.10+ is available
- Install uv package manager if needed
- Install all dependencies
- Run database migrations
- Optionally set up cron for automatic health checks

### Manual Install

```bash
uv sync
```

## Cron Setup

To schedule automatic health checks, run the cron setup script:

```bash
./bin/setup_cron.sh
```

Or choose "yes" when prompted during installation.

This will:
- Detect the project directory automatically
- Let you choose a schedule (every 5/15/60 minutes, daily, or custom)
- Add the health check command to your crontab
- Log output to `cron.log`

## Quickstart

Apply migrations:

```bash
uv run python manage.py migrate
```

Run the health check suite (see full docs in `apps/checkers/README.md`):

```bash
uv run python manage.py check_health
```

List available checkers:

```bash
uv run python manage.py check_health --list
```

(Optional) Run the Django server:

```bash
uv run python manage.py runserver
```

## Project layout

- `config/` — Django project settings/urls/asgi/wsgi
- `apps/` — Django apps
  - `apps/checkers/` — health checks + management commands
  - `apps/alerts/` — alert ingestion (scaffold)
  - `apps/notify/` — notification drivers (scaffold)
  - `apps/intelligence/` — intelligence/providers (scaffold)

## Contributing / extending

- Start by reading the app-level README for the area you’re changing.
- If you’re using AI agents in this repo, `agents.md` describes the available agent roles and project rules.

## License

MIT License (c) 2026 Burak.

See [`LICENSE`](LICENSE).
