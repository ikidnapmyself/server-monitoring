---
title: "2026-04-04 Installer Refactor Design"
parent: Plans
---

{% raw %}

# Installer Refactor: Subcommand Architecture

## Problem

`bin/install.sh` is a ~400-line monolith. Users in prod who need to configure a single thing (cluster, celery) must re-run the entire wizard. Related scripts (`deploy-docker.sh`, `deploy-systemd.sh`, `setup_cron.sh`, `setup_aliases.sh`) are scattered across `bin/` with no unified entry point.

## Goals

1. **Subcommands**: `install.sh cluster`, `install.sh celery`, etc. — run one step without the full flow.
2. **Existing config as default**: Every prompt shows the current `.env` value. Press Enter to keep it. No unnecessary questions.
3. **Single entry point**: All install/deploy/setup scripts consolidated under `install.sh`. Old standalone scripts deleted.
4. **Secure**: No code injection. Hardcoded command whitelist, no eval, no dynamic sourcing.

## Design

### Shared Prompt Library: `bin/lib/prompt.sh`

Two reusable functions used by all subcommands:

**`prompt_with_default ENV_FILE KEY "label" [fallback]`**
- Key has value in .env → show as default, Enter keeps it
- Key missing/empty + fallback provided → fallback is default
- Key missing/empty + no fallback → require input (loop until non-empty)
- Uses `read -r`, writes through `dotenv_set` (`printf`, no eval)

**`prompt_choice ENV_FILE KEY "label" [default] "opt1:desc1" "opt2:desc2" ...`**
- Shows numbered menu with descriptions
- If key exists in .env, that value is the default (highlighted)
- Validates input against known options, rejects anything else
- Returns selected value

Priority: user input > existing .env value > hardcoded fallback.

### File Structure

```
bin/
  install.sh              # Dispatcher + full-flow orchestrator
  install/
    env.sh                # DJANGO_ENV, DEPLOY_METHOD, DEBUG, HOSTS, SECRET_KEY
    celery.sh             # Broker URL, result backend, eager mode
    cluster.sh            # Role, HUB_URL, INSTANCE_ID, webhook secret
    deps.sh               # uv sync (reads DJANGO_ENV from .env)
    migrate.sh            # Django migrations + system checks
    cron.sh               # Cron schedule, auto-update, push-to-hub
    aliases.sh            # Prefix, shell profile sourcing, --remove/--list
    deploy.sh             # Docker compose or systemd (reads DEPLOY_METHOD from .env)
  lib/
    prompt.sh             # NEW — prompt_with_default, prompt_choice
    dotenv.sh             # Existing (unchanged)
    logging.sh            # Existing
    checks.sh             # Existing
    paths.sh              # Existing
    colors.sh             # Existing
    docker.sh             # Existing
```

### Dispatcher (`install.sh`)

```bash
case "${1:-}" in
    env)      source "$INSTALL_DIR/env.sh"     ;;
    celery)   source "$INSTALL_DIR/celery.sh"  ;;
    cluster)  source "$INSTALL_DIR/cluster.sh" ;;
    deps)     source "$INSTALL_DIR/deps.sh"    ;;
    migrate)  source "$INSTALL_DIR/migrate.sh" ;;
    cron)     source "$INSTALL_DIR/cron.sh"    ;;
    aliases)  source "$INSTALL_DIR/aliases.sh" ;;
    deploy)   source "$INSTALL_DIR/deploy.sh"  ;;
    help|-h)  show_usage                       ;;
    "")       run_all                          ;;
    *)        error "Unknown step: $1"; show_usage; exit 1 ;;
esac
```

Hardcoded whitelist — no user input reaches `source`, `eval`, or command substitution.

### Full Flow (`install.sh` with no args)

Runs all steps in order:

1. **env** — DJANGO_ENV, DEPLOY_METHOD, DEBUG, ALLOWED_HOSTS, SECRET_KEY
2. **celery** — broker, backend, eager mode
3. **cluster** — "Configure cluster? [y/N]", then role/HUB_URL/INSTANCE_ID/secret
4. **deps** — `uv sync` (dev extras based on DJANGO_ENV from .env)
5. **migrate** — `manage.py migrate` + `manage.py check`
6. **cron** — schedule, auto-update, push-to-hub
7. **aliases** — prefix, shell profile
8. **deploy** — docker compose build/up or systemd install (based on DEPLOY_METHOD)

Each step reads from `.env`, shows existing values as defaults, only writes back what changed. No `exec` — deploy step returns control for final summary.

### Subcommand Mode

Each module is self-contained:
- Sources `lib/prompt.sh`, `lib/dotenv.sh`, `lib/logging.sh`
- Ensures `.env` exists (calls `dotenv_ensure_file`)
- Reads its own config keys from `.env`
- Prompts with existing values as defaults
- Writes back only changed values
- Exits cleanly

Example: `install.sh cluster` on a configured system:

```
============================================
   Cluster Setup
============================================

Configure cluster mode? [Y/n]:        (default: y, because already configured)
Cluster role:
  1) agent — run checkers, push to hub
  2) hub   — accept alerts from agents
  3) both  — agent + hub
Current: agent
Enter choice [1/2/3] (default: 1):

HUB_URL [https://hub.example.com]:     (Enter to keep)
INSTANCE_ID [web-prod-01]:             (Enter to keep)
WEBHOOK_SECRET_CLUSTER [••••••••]:     (masked, Enter to keep)

[OK] Cluster configuration unchanged
```

### Security Model

- **Dispatcher**: `case` whitelist, unknown subcommands rejected
- **Prompts**: `read -r` (no backslash interpretation), values written via `printf "%s=%s\n"`
- **No eval/exec**: Values are never interpolated into commands
- **No dynamic source**: Module paths are hardcoded, not derived from user input
- **Secrets display**: Sensitive values (SECRET_KEY, WEBHOOK_SECRET) shown masked in prompts

### Files to Delete

| File | Absorbed into |
|------|--------------|
| `bin/deploy-docker.sh` | `bin/install/deploy.sh` |
| `bin/deploy-systemd.sh` | `bin/install/deploy.sh` |
| `bin/setup_cron.sh` | `bin/install/cron.sh` |
| `bin/setup_aliases.sh` | `bin/install/aliases.sh` |

### References to Update

| File | What to change |
|------|---------------|
| `bin/cli.sh` | Tip text: `bin/setup_aliases.sh` → `bin/install.sh aliases` |
| `bin/cli/install_menu.sh` | Call `install.sh aliases` instead of `setup_aliases.sh` |
| `bin/lib/health_check.sh` | Warning text: `bin/setup_aliases.sh` → `bin/install.sh aliases` |
| `bin/set_production.sh` | `bin/deploy-systemd.sh` → `bin/install.sh deploy` |
| `CLAUDE.md` | Update commands section |
| `README.md` | `bin/setup_aliases.sh` → `bin/install.sh aliases` |
| `bin/README.md` | Rewrite script reference sections |
| `docs/Installation.md` | All `setup_cron.sh`, `setup_aliases.sh` references |
| `docs/Deployment.md` | `deploy-systemd.sh`, `setup_cron.sh` references |
| `docs/Setup-Guide.md` | `setup_cron.sh` reference |
| `agents.md` | Update if references old scripts |
| `apps/*/agents.md` | Update if references old scripts |

{% endraw %}