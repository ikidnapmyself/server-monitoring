---
title: "2026-04-02 Cluster Setup Installer Integration & Docs"
parent: Plans
---

# Cluster Setup Installer Integration & Docs

**Date:** 2026-04-02
**Status:** Approved

## Problem

Setting up a cluster agent or hub requires manually editing `.env` and adding cron entries. The installer (`bin/install.sh`) has no awareness of cluster roles, and the docs in `Deployment.md` lack verification steps, security guidance, and troubleshooting.

## Goal

1. Add cluster role prompts to `bin/install.sh` so agents and hubs can be configured during initial setup
2. Add `push_to_hub` scheduling to `bin/setup_cron.sh`
3. Expand the Multi-Instance section in `docs/Deployment.md`

## Design

### Installer changes (`bin/install.sh`)

After the existing post-install prompts (health check, cron, aliases, systemd), add a cluster role section. This runs for both bare-metal and Docker installs (before the Docker handoff, cluster vars are just `.env` entries).

#### Flow

```
Configure this instance for multi-instance (cluster) mode? [y/N]
  → N: skip, standalone mode (default, zero changes)
  → Y: continue:

Select cluster role:
  1) agent — run checkers locally, push results to a hub
  2) hub   — accept alerts from remote agents
  3) both  — agent + hub (push to another hub while accepting agents)

[agent or both]
  HUB_URL (e.g. https://monitoring-hub.example.com): <required>
  INSTANCE_ID (default: <hostname>): <optional>

[hub or both]
  (no extra prompts — just enables CLUSTER_ENABLED=1)

[agent, hub, or both]
  WEBHOOK_SECRET_CLUSTER: <required, shared secret>

Write to .env, run push_to_hub --dry-run for agents to verify.
```

#### .env writes

| Variable | Agent | Hub | Both |
|----------|-------|-----|------|
| `HUB_URL` | user value | skip | user value |
| `CLUSTER_ENABLED` | skip | `1` | `1` |
| `INSTANCE_ID` | user value or hostname | skip | user value or hostname |
| `WEBHOOK_SECRET_CLUSTER` | user value | user value | user value |

All writes use `dotenv_set` (overwrites empty values from `.env.sample`).

#### Verification

For agent/both roles, run `push_to_hub --dry-run` after writing `.env`. This validates that the payload builds correctly without actually POSTing. Show the output so the user can confirm checkers are detected.

### Cron changes (`bin/setup_cron.sh`)

After the auto-update prompt, check if `HUB_URL` is set in `.env`. If so, offer to schedule `push_to_hub`:

```
HUB_URL detected — schedule automatic push to hub? [Y/n]
```

If yes, add a cron entry on the same schedule:

```cron
*/5 * * * * cd /path/to/project && uv run python manage.py push_to_hub --json >> push.log 2>&1 # server-maintanence cluster push
```

### Docs changes (`docs/Deployment.md`)

Expand the Multi-Instance (Cluster) section with:

**Verification** — commands to confirm agent and hub are working:

```bash
# Agent: dry-run to verify payload
uv run python manage.py push_to_hub --dry-run

# Agent: single push to verify connectivity
uv run python manage.py push_to_hub

# Hub: verify cluster driver is registered
uv run python manage.py shell -c "from apps.alerts.drivers import DRIVER_REGISTRY; print('cluster' in DRIVER_REGISTRY)"
```

**Security notes:**
- Always use HTTPS for `HUB_URL` in production
- `WEBHOOK_SECRET_CLUSTER` must match on agent and hub
- HMAC-SHA256 signature is sent via `X-Cluster-Signature` header
- Without a shared secret, payloads are accepted unsigned (dev only)

**Troubleshooting:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| `push_to_hub` → "HUB_URL not configured" | Missing `.env` entry | Set `HUB_URL` in `.env` |
| `push_to_hub` → connection refused | Hub not running or wrong URL | Verify hub is accessible, check URL |
| `push_to_hub` → 403 Forbidden | Signature mismatch | Ensure `WEBHOOK_SECRET_CLUSTER` matches on both sides |
| `push_to_hub` → 404 Not Found | Cluster driver not registered on hub | Set `CLUSTER_ENABLED=1` on hub, restart |
| Alerts arrive but no notifications | Pipeline not configured on hub | Run `setup_instance` on hub to create pipeline |

## File Changes

| File | Change |
|------|--------|
| `bin/install.sh` | Add cluster role prompts after existing post-install section |
| `bin/setup_cron.sh` | Add `push_to_hub` cron option when `HUB_URL` is set |
| `docs/Deployment.md` | Expand cluster section with verification, security, troubleshooting |

## Non-Goals

- Changing the cluster driver or `push_to_hub` command
- Adding a standalone `bin/setup_agent.sh` script
- Auto-generating `WEBHOOK_SECRET_CLUSTER` (user should use their own shared secret)