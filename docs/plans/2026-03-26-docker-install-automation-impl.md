---
title: "Docker Install Automation — Implementation"
parent: Plans
nav_order: 79739673
---

# Docker Install Automation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `docker` mode to `bin/install.sh` that automates Docker Compose deployment, and create `bin/deploy-docker.sh` for Docker-specific build/start/health-check logic.

**Architecture:** `install.sh` gains a third mode (`docker`) that reuses existing `.env` prompt helpers, skips Python/uv/migration steps, and delegates to the new `bin/deploy-docker.sh`. The deploy script handles pre-flight checks, `docker compose build/up`, health polling, and summary output.

**Tech Stack:** Bash, Docker Compose v2, existing `.env` helpers from `install.sh`

---

### Task 1: Create `bin/deploy-docker.sh` — pre-flight checks

**Files:**
- Create: `bin/deploy-docker.sh`

**Step 1: Create the script with pre-flight checks**

```bash
#!/bin/bash
#
# Docker Compose deployment script for server-maintanence
# Can be run standalone or called from install.sh
#

set -e

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"

cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Pre-flight checks ---

info "Running pre-flight checks..."

# 1. .env must exist
if [ ! -f "$PROJECT_DIR/.env" ]; then
    error ".env file not found."
    echo "  Run ./bin/install.sh first, or copy .env.sample to .env and configure it."
    exit 1
fi
success ".env file found"

# 2. Docker daemon must be running
if ! docker info >/dev/null 2>&1; then
    error "Docker is not running."
    echo "  Docker is required. Install it from https://docs.docker.com/get-docker/"
    echo "  and ensure the daemon is running."
    exit 1
fi
success "Docker daemon is running"

# 3. docker compose v2 must be available
if ! docker compose version >/dev/null 2>&1; then
    error "docker compose (v2) is not available."
    echo "  This project requires Docker Compose v2 (the 'docker compose' plugin)."
    echo "  The legacy standalone 'docker-compose' (v1) is not supported."
    echo "  See: https://docs.docker.com/compose/install/"
    exit 1
fi
COMPOSE_VERSION=$(docker compose version --short 2>/dev/null)
success "Docker Compose v2 available ($COMPOSE_VERSION)"

echo ""
```

**Step 2: Make the script executable**

Run: `chmod +x bin/deploy-docker.sh`

**Step 3: Test the pre-flight checks**

Run: `./bin/deploy-docker.sh`
Expected: All three pre-flight checks pass (assuming Docker is running and .env exists), then script exits (no build/start logic yet).

**Step 4: Commit**

```bash
git add bin/deploy-docker.sh
git commit -m "feat: add deploy-docker.sh with pre-flight checks"
```

---

### Task 2: Add build & start to `bin/deploy-docker.sh`

**Files:**
- Modify: `bin/deploy-docker.sh` (append after pre-flight checks)

**Step 1: Add build and start logic**

Append after the `echo ""` at the end of the pre-flight section:

```bash
# --- Build & Start ---

info "Building Docker images..."
docker compose -f "$COMPOSE_FILE" build
success "Docker images built"

echo ""
info "Starting Docker Compose stack..."
docker compose -f "$COMPOSE_FILE" up -d
success "Docker Compose stack started"

echo ""
```

**Step 2: Test build and start**

Run: `./bin/deploy-docker.sh`
Expected: Images build, containers start, script completes. Verify with `docker compose -f deploy/docker/docker-compose.yml ps`.

**Step 3: Commit**

```bash
git add bin/deploy-docker.sh
git commit -m "feat: add docker build and start to deploy-docker.sh"
```

---

### Task 3: Add health verification to `bin/deploy-docker.sh`

**Files:**
- Modify: `bin/deploy-docker.sh` (append after start section)

**Step 1: Add health verification logic**

Append after the start section:

```bash
# --- Health Verification ---

info "Verifying stack health (timeout: 60s)..."

TIMEOUT=60
INTERVAL=5
ELAPSED=0
REDIS_OK=false
WEB_OK=false
CELERY_OK=false

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    # Check redis
    if [ "$REDIS_OK" = false ]; then
        if docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
            REDIS_OK=true
            success "redis — healthy (PONG)"
        fi
    fi

    # Check web (running and not restarting)
    if [ "$WEB_OK" = false ]; then
        WEB_STATE=$(docker compose -f "$COMPOSE_FILE" ps --format json web 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || echo "")
        if [ "$WEB_STATE" = "running" ]; then
            WEB_OK=true
            success "web — running"
        fi
    fi

    # Check celery (running and not restarting)
    if [ "$CELERY_OK" = false ]; then
        CELERY_STATE=$(docker compose -f "$COMPOSE_FILE" ps --format json celery 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || echo "")
        if [ "$CELERY_STATE" = "running" ]; then
            CELERY_OK=true
            success "celery — running"
        fi
    fi

    # All healthy?
    if [ "$REDIS_OK" = true ] && [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
        break
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo ""

# Report failures
FAILED=false
if [ "$REDIS_OK" = false ]; then
    error "redis — not healthy after ${TIMEOUT}s"
    FAILED=true
fi
if [ "$WEB_OK" = false ]; then
    error "web — not healthy after ${TIMEOUT}s"
    echo "  Check logs: docker compose -f $COMPOSE_FILE logs web"
    FAILED=true
fi
if [ "$CELERY_OK" = false ]; then
    error "celery — not healthy after ${TIMEOUT}s"
    echo "  Check logs: docker compose -f $COMPOSE_FILE logs celery"
    FAILED=true
fi

if [ "$FAILED" = true ]; then
    error "One or more services failed health checks."
    exit 1
fi
```

**Step 2: Test health verification**

Run: `./bin/deploy-docker.sh`
Expected: After build/start, script polls and reports pass/fail for redis, web, celery. All three show "healthy" / "running".

**Step 3: Commit**

```bash
git add bin/deploy-docker.sh
git commit -m "feat: add health verification to deploy-docker.sh"
```

---

### Task 4: Add summary output to `bin/deploy-docker.sh`

**Files:**
- Modify: `bin/deploy-docker.sh` (append after health verification)

**Step 1: Add summary output**

Append at the end of the script:

```bash
# --- Summary ---

echo ""
echo "============================================"
echo -e "${GREEN}   Docker Stack Running!${NC}"
echo "============================================"
echo ""

WEB_PORT=$(grep -E "^WEB_PORT=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2)
WEB_PORT="${WEB_PORT:-8000}"

echo "Services:"
echo "  - Web:    http://localhost:${WEB_PORT}"
echo "  - Redis:  redis://localhost:6379 (internal)"
echo "  - Celery: background worker"
echo ""
echo "Useful commands:"
echo "  docker compose -f $COMPOSE_FILE logs -f       # Follow logs"
echo "  docker compose -f $COMPOSE_FILE ps             # Service status"
echo "  docker compose -f $COMPOSE_FILE down           # Stop stack"
echo "  docker compose -f $COMPOSE_FILE up -d --build  # Rebuild & restart"
echo ""
```

**Step 2: Test summary output**

Run: `./bin/deploy-docker.sh`
Expected: After health checks pass, prints service URLs and useful commands.

**Step 3: Commit**

```bash
git add bin/deploy-docker.sh
git commit -m "feat: add summary output to deploy-docker.sh"
```

---

### Task 5: Add docker mode to `bin/install.sh`

**Files:**
- Modify: `bin/install.sh`

**Step 1: Add `dotenv_prompt_docker()` function**

Insert after the `dotenv_prompt_setup()` function (after line 228), before the main script body:

```bash
dotenv_prompt_docker() {
    local env_file="$PROJECT_DIR/.env"

    echo ""
    echo "============================================"
    echo "   Docker environment setup (.env)"
    echo "============================================"
    echo ""
    info "Configuring .env for Docker Compose deployment."
    info "CELERY_BROKER_URL is managed by Docker Compose — skipping."
    echo ""

    # DJANGO_DEBUG
    if ! dotenv_has_key "$env_file" "DJANGO_DEBUG"; then
        read -p "DJANGO_DEBUG (1=on, 0=off, default: 1): " -r DEBUG_INPUT
        DEBUG_INPUT="${DEBUG_INPUT:-1}"
        dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "$DEBUG_INPUT"
    fi

    # DJANGO_SECRET_KEY
    if ! dotenv_has_key "$env_file" "DJANGO_SECRET_KEY"; then
        read -p "Generate a secure DJANGO_SECRET_KEY now? [Y/n]: " -n 1 -r
        echo ""
        if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
            if command_exists python3; then
                local key
                key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
                dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$key"
                success "DJANGO_SECRET_KEY added to .env"
            else
                error "python3 not available; cannot generate DJANGO_SECRET_KEY."
                local manual_key
                manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
                dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
            fi
        else
            local manual_key
            manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
            dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
        fi
    fi

    # DJANGO_ALLOWED_HOSTS
    if ! dotenv_has_key "$env_file" "DJANGO_ALLOWED_HOSTS"; then
        read -p "DJANGO_ALLOWED_HOSTS (comma-separated, default: localhost,127.0.0.1): " -r HOSTS_INPUT
        HOSTS_INPUT="${HOSTS_INPUT:-localhost,127.0.0.1}"
        dotenv_set_if_missing "$env_file" "DJANGO_ALLOWED_HOSTS" "$HOSTS_INPUT"
    fi

    # Safety: never eager in docker (Compose provides Redis)
    dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"

    success ".env setup complete (Docker mode)"
}
```

**Step 2: Replace the mode selection and main flow**

Replace the main script body (lines 230–373) with a version that includes docker mode. The key changes are:

1. Move Python/uv checks into a function so docker mode can skip them.
2. Add mode selection prompt with 3 options.
3. Docker mode: check Docker, run `dotenv_prompt_docker()`, delegate to `deploy-docker.sh`.
4. Dev/prod modes: existing behavior unchanged.

Replace from line 230 (`echo ""`) to end of file with:

```bash
check_python() {
    info "Checking Python version..."
    PYTHON_BIN=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command_exists "$candidate"; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        error "Python 3 is not installed. Please install Python 3.10 or higher."
        exit 1
    fi

    PYTHON_VERSION=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        success "Python $PYTHON_VERSION found via $PYTHON_BIN (>= 3.10 required)"
    else
        error "Python 3.10+ is required, but found Python $PYTHON_VERSION ($PYTHON_BIN)"
        info "Tip: If Python 3.10+ is installed under a different name, set PYTHON_BIN and re-run."
        exit 1
    fi
}

check_uv() {
    info "Checking for uv package manager..."
    if command_exists uv; then
        UV_VERSION=$(uv --version 2>/dev/null | head -n1)
        success "uv is already installed: $UV_VERSION"
    else
        warn "uv is not installed. Installing uv..."

        if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
            curl -LsSf https://astral.sh/uv/install.sh | sh

            if [ -f "$HOME/.cargo/env" ]; then
                source "$HOME/.cargo/env"
            fi

            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

            if command_exists uv; then
                success "uv installed successfully"
            else
                error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
                exit 1
            fi
        else
            error "Unsupported OS. Please install uv manually: https://docs.astral.sh/uv/"
            exit 1
        fi
    fi
}

echo ""
echo "============================================"
echo "   server-maintanence Installer"
echo "============================================"
echo ""

# Step 1: Mode selection
echo "Select installation mode:"
echo "  1) dev    — local development (DEBUG=1, eager tasks)"
echo "  2) prod   — bare-metal production (systemd, gunicorn)"
echo "  3) docker — Docker Compose stack (requires Docker running)"
echo ""

INSTALL_MODE=""
while true; do
    read -p "Mode [1/2/3] (default: 1): " -r MODE_INPUT
    MODE_INPUT="${MODE_INPUT:-1}"
    case "$MODE_INPUT" in
        1|d|dev)    INSTALL_MODE="dev"; break ;;
        2|p|prod)   INSTALL_MODE="prod"; break ;;
        3|docker)   INSTALL_MODE="docker"; break ;;
        *) warn "Please enter 1, 2, or 3." ;;
    esac
done

info "Selected mode: $INSTALL_MODE"
echo ""

if [ "$INSTALL_MODE" = "docker" ]; then
    # Docker mode: check Docker, setup .env, delegate to deploy-docker.sh

    # Check Docker daemon
    if ! docker info >/dev/null 2>&1; then
        error "Docker is not running."
        echo "  Docker is required. Install it from https://docs.docker.com/get-docker/"
        echo "  and ensure the daemon is running."
        exit 1
    fi
    success "Docker daemon is running"

    # Setup .env
    dotenv_ensure_file
    dotenv_prompt_docker

    # Delegate to deploy-docker.sh
    echo ""
    info "Handing off to deploy-docker.sh..."
    exec "$SCRIPT_DIR/deploy-docker.sh"
else
    # Dev/Prod mode: existing flow

    # Step 2: Check Python version
    check_python

    # Step 3: Check/Install uv
    check_uv

    # Step 4: .env setup
    dotenv_ensure_file
    dotenv_prompt_setup

    # Step 5: Sync dependencies
    if [ "$INSTALL_MODE" = "dev" ]; then
        info "Installing dependencies with uv sync (including development dependencies)..."
        uv sync --all-extras --dev
    else
        info "Installing dependencies with uv sync (production only)..."
        uv sync
    fi
    success "Dependencies installed"

    # Step 6: Run migrations
    info "Running database migrations..."
    uv run python manage.py migrate
    success "Migrations applied"

    # Step 7: Run Django system checks
    info "Running Django system checks..."
    if uv run python manage.py check; then
        success "All system checks passed"
    else
        warn "System checks reported issues (see above). You may want to address them."
    fi

    # Step 8: Summary and next steps
    echo ""
    echo "============================================"
    echo -e "${GREEN}   Installation Complete!${NC}"
    echo "============================================"
    echo ""
    info "Your server-maintanence project is now set up."
    echo ""
    echo "Quick commands:"
    echo "  - Run health checks:    uv run python manage.py check_health"
    echo "  - List checkers:        uv run python manage.py check_health --list"
    echo "  - Start server:         uv run python manage.py runserver"
    echo "  - Run tests:            uv run pytest"
    echo ""
    echo "Documentation:"
    echo "  - Main README:          README.md"
    echo "  - Agent conventions:    agents.md"
    echo ""

    read -p "Would you like to run the health check suite now? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Running health checks..."
        uv run python manage.py check_health
    fi

    echo ""
    read -p "Would you like to set up automatic health checks via cron? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/setup_cron.sh"
    fi

    # Show existing pipeline definitions if any
    echo ""
    PIPELINE_COUNT=$(uv run python manage.py show_pipeline --json 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    if [ "$PIPELINE_COUNT" != "0" ] && [ "$PIPELINE_COUNT" != "" ]; then
        info "Found $PIPELINE_COUNT configured pipeline(s):"
        uv run python manage.py show_pipeline
        echo ""
    else
        info "No pipelines configured yet. Run the setup wizard to create one:"
        echo "  uv run python manage.py setup_instance"
        echo ""
    fi

    echo ""
    read -p "Would you like to set up shell aliases (e.g., sm-check-health)? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/setup_aliases.sh"
    fi

    success "Setup complete!"
fi
```

**Step 3: Verify dev/prod modes still work**

Run: `./bin/install.sh` and select `1` (dev).
Expected: Existing dev flow works exactly as before — Python check, uv check, .env prompts, deps, migrations, health checks.

**Step 4: Verify docker mode works**

Run: `./bin/install.sh` and select `3` (docker).
Expected: Docker check, .env prompts (DEBUG, SECRET_KEY, ALLOWED_HOSTS — no CELERY_BROKER_URL), then hands off to `deploy-docker.sh` which builds, starts, health-checks, and prints summary.

**Step 5: Commit**

```bash
git add bin/install.sh
git commit -m "feat: add docker mode to install.sh"
```

---

### Task 6: Update `docs/Deployment.md`

**Files:**
- Modify: `docs/Deployment.md` (lines 56–71, the Docker Compose section intro)

**Step 1: Add automation note**

After the existing line "The fastest way to get a production stack running..." (line 58), add a tip about the automated path. Replace the "### 1.1 Clone and configure" section intro to include:

Insert after line 58 (`The fastest way to get a production stack running. Includes Django (gunicorn), Celery worker, and Redis.`):

```markdown

> **Quick start:** Run `./bin/install.sh` and select **docker** mode to automate the steps below (`.env` setup, build, start, and health verification).
```

**Step 2: Commit**

```bash
git add docs/Deployment.md
git commit -m "docs: add install.sh docker mode note to Deployment.md"
```

---

### Task 7: Update `bin/README.md`

**Files:**
- Modify: `bin/README.md`

**Step 1: Add deploy-docker.sh to the scripts table**

Read `bin/README.md` and add a row for `deploy-docker.sh` in the scripts reference table, describing it as: "Docker Compose deployment — builds images, starts stack, verifies health. Called by `install.sh` (docker mode) or run standalone."

**Step 2: Commit**

```bash
git add bin/README.md
git commit -m "docs: add deploy-docker.sh to bin/README.md"
```