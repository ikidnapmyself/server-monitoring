# CLI Aliases & CLI-First Documentation Design

## Goal

Add optional shell aliases for all management commands and overhaul every app README to be CLI-first — documenting every flag and useful combination for all 8 commands (67 flags total).

## Audience

Developers and ops engineers who interact with the system primarily via CLI.

## Approach

Two parts: (A) shell alias infrastructure, (B) CLI-first documentation overhaul across all READMEs.

## Part A: Shell Aliases

### How it works

1. `bin/setup_aliases.sh` is the interactive setup script
2. It generates `bin/aliases.sh` (gitignored, project-path-locked)
3. It adds a `source` line to the user's shell profile (`~/.bashrc` or `~/.zshrc`)
4. `--remove` undoes everything

### Alias map (default prefix: `sm`)

| Alias | Expands to |
|-------|-----------|
| `sm-check-health` | `uv run python manage.py check_health` |
| `sm-run-check` | `uv run python manage.py run_check` |
| `sm-check-and-alert` | `uv run python manage.py check_and_alert` |
| `sm-get-recommendations` | `uv run python manage.py get_recommendations` |
| `sm-run-pipeline` | `uv run python manage.py run_pipeline` |
| `sm-monitor-pipeline` | `uv run python manage.py monitor_pipeline` |
| `sm-test-notify` | `uv run python manage.py test_notify` |
| `sm-list-notify-drivers` | `uv run python manage.py list_notify_drivers` |
| `sm-cli` | `./bin/cli.sh` |

Custom prefix via `--prefix`: e.g. `--prefix maint` → `maint-check-health`, etc.

### Django system check

`@register("aliases")` in `apps/checkers/checks.py`:
- Runs only in dev (`settings.DEBUG=True`)
- Warns if `bin/aliases.sh` doesn't exist
- ID: `checkers.W009`

### cli.sh changes

- Startup hint: "Tip: Run bin/setup_aliases.sh for quick command aliases"
- New option in Install / Setup menu: "Setup shell aliases"

### Files

| Action | File |
|--------|------|
| Create | `bin/setup_aliases.sh` |
| Create | `bin/aliases.sh` (generated, gitignored) |
| Modify | `apps/checkers/checks.py` |
| Modify | `bin/cli.sh` |
| Modify | `.gitignore` |

## Part B: CLI-First Documentation Overhaul

Every app README gets a complete CLI reference section with every flag and every useful combination documented with examples.

### Commands and flag counts

| Command | App | Flags |
|---------|-----|-------|
| `check_health` | checkers | 10 |
| `run_check` | checkers | 11 |
| `check_and_alert` | alerts | 9 |
| `get_recommendations` | intelligence | 11 |
| `list_notify_drivers` | notify | 1 |
| `test_notify` | notify | 14 |
| `run_pipeline` | orchestration | 12 |
| `monitor_pipeline` | orchestration | 3 |

### Documentation per README

**`apps/checkers/README.md`**: `check_health` (all 10 flags), `run_check` (all 11 flags) — per-checker examples for cpu, memory, disk, network, process with all checker-specific flags.

**`apps/alerts/README.md`**: `check_and_alert` (all 9 flags) — dry-run, labels, hostname, thresholds, cron usage.

**`apps/intelligence/README.md`**: `get_recommendations` (all 11 flags) — memory, disk, combined, incident, providers, JSON.

**`apps/notify/README.md`**: `list_notify_drivers` (1 flag), `test_notify` (all 14 flags) — per-driver examples for email, slack, pagerduty, generic.

**`apps/orchestration/README.md`**: `run_pipeline` (all 12 flags), `monitor_pipeline` (all 3 flags) — sample, file, definition-based, dry-run, trace-id.

**`bin/README.md`**: Quick reference table of all 8 commands + 9 aliases, setup_aliases.sh usage, cross-references.

**`README.md`**: Aliases in quickstart, link to bin/README.md.

## File Operations

| Action | File |
|--------|------|
| Create | `bin/setup_aliases.sh` |
| Modify | `apps/checkers/checks.py` |
| Modify | `apps/checkers/checks.py` tests |
| Modify | `bin/cli.sh` |
| Modify | `.gitignore` |
| Modify | `apps/checkers/README.md` |
| Modify | `apps/alerts/README.md` |
| Modify | `apps/intelligence/README.md` |
| Modify | `apps/notify/README.md` |
| Modify | `apps/orchestration/README.md` |
| Modify | `bin/README.md` |
| Modify | `README.md` |
