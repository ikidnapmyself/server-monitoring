# server-maintanence

A small, modular Django project for basic **server maintenance / monitoring** workflows.

This top-level README is the entry point and documentation hub. App-specific docs live alongside the apps.

## Documentation map

- Health checks (checkers): [`apps/checkers/README.md`](apps/checkers/README.md)
- Alert ingestion: [`apps/alerts/README.md`](apps/alerts/README.md)
- Notifications: [`apps/notify/README.md`](apps/notify/README.md)
- Intelligence/recommendations: [`apps/intelligence/README.md`](apps/intelligence/README.md)
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

### Django System Checks

After the installation steps, you can verify project configuration.
Run all system checks:

```bash
uv run python manage.py check
```

## Development

This repo uses `uv` for dependency management and a small, consistent dev-tooling stack configured in `pyproject.toml`:

- **Black** for formatting
- **Ruff** for linting + import sorting
- **pytest + pytest-django** for tests
- **mypy + django-stubs** (optional) for type-checking

### Common commands

```bash
# Install runtime + dev tools
uv sync --extra dev

# Set up pre-commit hooks
uv run pre-commit install

# Run pre-commit manually on all files
uv run pre-commit run --all-files

# Format
uv run black .

# Lint (and auto-fix imports where possible)
uv run ruff check . --fix

# Tests
uv run pytest

# Optional: type-check
uv run mypy .
```


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
  - `apps/intelligence/` — intelligence/recommendations system
- `bin/` - shell scripts

## Contributing / extending

- Start by reading the app-level README for the area you’re changing.
- If you’re using AI agents in this repo, `agents.md` describes the available agent roles and project rules.

## License

MIT License (c) 2026 Burak.

See [`LICENSE`](LICENSE).
