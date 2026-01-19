# Installation

This repo supports **two install paths**:

1) **Quick install (recommended)** — uses the provided shell scripts
2) **Manual install** — run the commands yourself (useful for CI or custom setups)

---

## Requirements

- Python **3.10+**
- [`uv`](https://github.com/astral-sh/uv)
- Dependencies are defined in `pyproject.toml`

---

## 1) Quick install (recommended)

### 1.1 Clone the repo

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
```

### 1.2 Run the installer

```bash
./bin/install.sh
```

If you get “permission denied”, run:

```bash
chmod +x ./bin/install.sh ./bin/setup_cron.sh ./bin/deploy.sh
```

### What the installer does (in order)

`./bin/install.sh` performs these steps:

- Verifies Python is **3.10+**
- Installs `uv` if missing
- Ensures you have a `.env` (creates it from `.env.sample` if present)
- Prompts you for **dev** or **production** configuration and appends missing `.env` keys
  - It **does not overwrite** existing values
- Installs dependencies with `uv sync`
  - dev installs include **dev extras**
  - prod installs are **runtime-only**
- Runs Django migrations
- Runs `python manage.py check`
- Optionally runs health checks now
- Optionally sets up cron via `./bin/setup_cron.sh`

See the installer implementation in `bin/install.sh`.

---

## 2) Cron setup (optional)

If you didn’t enable cron during install, you can run it later:

```bash
./bin/setup_cron.sh
```

### What it does

- Detects the project directory automatically
- Lets you choose a schedule (every 5 min / 15 min / hourly / etc. or custom)
- Writes a `crontab` entry that runs:

```bash
uv run python manage.py check_and_alert --json
```

- Logs output to `cron.log` in the project root

See the cron script in `bin/setup_cron.sh`.

### Useful commands

```bash
crontab -l
tail -f ./cron.log
```

---

## 3) Manual installation (no scripts)

Use this if you want full control or you’re running in CI.

### 3.1 Clone

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
```

### 3.2 Create and activate a virtualenv

```bash
python3 -m venv .venv
. .venv/bin/activate
```

### 3.3 Install uv (via pip)

```bash
python -m pip install --upgrade pip
pip install uv
```

### 3.4 Create your `.env`

```bash
cp .env.sample .env
```

Set at least a secret key (required for real deployments):

```bash
# example
echo 'DJANGO_SECRET_KEY=change-me' >> .env
```

### 3.5 Install dependencies

Production-style (no dev tools):

```bash
uv sync --frozen --no-dev
```

Dev install (includes dev tools/extras):

```bash
uv sync --all-extras --dev
```

### 3.6 Migrate

```bash
uv run --frozen python manage.py migrate --noinput
```

### 3.7 Django system check

```bash
uv run python manage.py check
```

### 3.8 Run the server

```bash
uv run python manage.py runserver
```

---

## 4) Common next commands

```bash
# Run health checks
uv run python manage.py check_health

# List available checkers
uv run python manage.py check_health --list

# Run checks + create alerts (cron-friendly)
uv run python manage.py check_and_alert --json
```
