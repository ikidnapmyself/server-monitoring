---
title: "2026-04-04 Install Profile Design"
parent: Plans
---

{% raw %}

# Install Profile: Saved Configuration for Consistency

## Problem

After running `install.sh`, there's no way to reproduce the exact same configuration on another machine or re-run the installer idempotently. Users in a fleet must manually answer the same prompts on every instance.

## Goals

1. **Save installer choices** to a profile file after setup
2. **Load a profile** to pre-fill all prompts on new machines
3. **Non-interactive mode** (`--yes`) for automation — accept all profile defaults, only prompt for secrets
4. **No secrets in profile** — sensitive keys are always excluded
5. **Custom naming** — profiles can have names for fleet management (e.g., `web-prod`, `agent-eu-01`)

## Profile File Format

File: `.install-profile` (default), `.install-profile-<name>` (named). Gitignored via `.install-profile*`.

```bash
# server-maintanence install profile
# name: prod-agent-web01
# created: 2026-04-04T14:30:00+0200
# hostname: web-prod-01
# installer_version: 1

# Environment
DJANGO_ENV=prod
DEPLOY_METHOD=bare
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=example.com,www.example.com

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
CELERY_TASK_ALWAYS_EAGER=0

# Cluster
CLUSTER_ROLE=agent
HUB_URL=https://hub.example.com
INSTANCE_ID=web-prod-01
CLUSTER_ENABLED=0

# Cron
CRON_SCHEDULE=*/5 * * * *
CRON_AUTO_UPDATE=1
CRON_PUSH_TO_HUB=1

# Aliases
ALIAS_PREFIX=sm
```

**Excluded (secrets):** `DJANGO_SECRET_KEY`, `WEBHOOK_SECRET_CLUSTER`

**Included beyond .env:** `CRON_SCHEDULE`, `CRON_AUTO_UPDATE`, `CRON_PUSH_TO_HUB`, `ALIAS_PREFIX` — installer choices not stored in `.env`.

## CLI Interface

```bash
# Save after install
install.sh --save-profile [name]

# Load and apply
install.sh --profile .install-profile
install.sh --profile .install-profile --yes    # non-interactive, only prompt secrets
install.sh cluster --profile .install-profile   # single step, pre-filled

# Named profiles
install.sh --save-profile web-prod             # saves .install-profile-web-prod
install.sh --profile web-prod                   # loads .install-profile-web-prod (or literal path)
```

**Behavior when profile loaded:**
- Profile values written to `.env` via `dotenv_set` before modules run
- `prompt_with_default` picks up values naturally from `.env`
- Secrets still prompted (never in profile)
- Missing keys in profile → prompt as normal (additive, never blocks)

**`--yes` mode:**
- If a default exists (from profile or .env), accept without prompting
- Only stop for missing required values and secrets
- Controlled by global `INSTALL_AUTO_ACCEPT=1` flag

## Implementation

### New file: `bin/lib/profile.sh`

Functions:
- `profile_load FILE` — sources profile, writes values to `.env` via `dotenv_set`
- `profile_save FILE [NAME]` — reads `.env` + installer state vars, writes non-sensitive keys with metadata header
- `profile_metadata FILE KEY` — reads metadata from comment header (e.g., `name`, `created`)

Sensitive keys list:
```bash
SENSITIVE_KEYS=(DJANGO_SECRET_KEY WEBHOOK_SECRET_CLUSTER)
```

On load: if profile contains a sensitive key, ignore it with a warning.

### Changes to `bin/lib/prompt.sh`

`prompt_with_default` and `prompt_choice` check `INSTALL_AUTO_ACCEPT`:
- If `1` and a default exists → return default without prompting
- If `1` and no default → still prompt (required value)

`prompt_yes_no` in auto-accept mode returns the default without prompting.

### Changes to `bin/install.sh`

Parse new flags before dispatcher:
```bash
--profile FILE      → call profile_load, set INSTALL_PROFILE_LOADED=1
--yes               → set INSTALL_AUTO_ACCEPT=1
--save-profile [N]  → set INSTALL_SAVE_PROFILE=1, INSTALL_PROFILE_NAME=N
```

After all steps complete: if `--save-profile` set or user accepts save prompt, call `profile_save`.

### Changes to installer modules

**Cron, aliases modules** need to export their state variables (`CRON_SCHEDULE`, `ALIAS_PREFIX`, etc.) so `profile_save` can capture them. Currently these are local to the module — they need to be set in the shell environment after the user chooses.

### Gitignore

Add `.install-profile*` to `.gitignore`.

## Custom Profile Names

`--save-profile web-prod` → saves to `.install-profile-web-prod`
`--profile web-prod` → looks for `.install-profile-web-prod` first, then tries as literal path

## Edge Cases

- **Profile from older version missing new keys** → modules prompt normally, profile is additive
- **Profile conflicts with .env** → profile wins (values written to .env before modules run)
- **Secrets in profile file** → ignored with warning on load

{% endraw %}