---
title: "Guided Cluster Setup — Design (Phase 3c)"
parent: Plans
---

# Guided Cluster Setup — Design (Phase 3c)

**Status:** Design, written 2026-07-30. Approved. Scoped slice of Phase 3 (unified
entrypoints); the role-aware main menu and entrypoint consolidation are **out of
scope**.

## Problem

Standing up a cluster means assembling pieces across separate tools — `install.sh
cluster` (writes `.env`), `create_api_key` (mints a key), `push_to_hub` (agent
push), `doctor` (status) — and nothing ties them into one "make this node work as
a hub/agent, and prove it" flow. The failures are silent (no channel, a WAF 403,
a key scoped away from `/alerts/webhook/cluster/`).

## Goal

One guided, **self-verifying** command that makes a node a working hub or agent
and only reports success when it actually works — turning the manual debugging
(401/403/WAF/scope) into named, fixable outcomes.

## Design

### `manage.py setup_cluster` (new; `config/management/commands/`)

Interactive by default; flags for unattended use. It is the single implementation;
the CLI calls it.

**Hub** (`--role hub`):
1. Ensure `API_KEY_AUTH_ENABLED=1` in `.env`.
2. Mint an `APIKey` (reusing the model) and **print the raw token once**.
3. **Confirm**: role `hub`, accepting pushes → yes (reuse `doctor`'s
   `_cluster_status`).

**Agent** (`--role agent`, `--hub-url`, `--instance-id`, `--hub-api-key`):
1. Write the three values to `.env`.
2. **Verify with a real push** (unless `--no-verify`) using the *values just
   entered* (not stale settings), and report:
   - `202/200` → ✓ configured.
   - `401` → invalid/missing key (fix: re-check the token).
   - `403` → WAF blocks the agent UA, or the key's `allowed_endpoints` excludes
     `/alerts/webhook/cluster/` (fix: allowlist UA / widen scope).
   - connection error → hub unreachable (fix: check `HUB_URL`).

### Supporting refactors (DRY, small)

1. Extract from `push_to_hub` into module-level functions:
   - `build_cluster_payload(instance_id, hostname, alerts) -> dict`
   - `send_to_hub(hub_url, api_key, payload, *, dry_run=False) -> (status, body)`
     (builds URL + Bearer/User-Agent headers + `safe_urlopen`; raises on transport
     errors). `push_to_hub` and `setup_cluster` both call it — the agent verify uses
     the just-entered values, so no stale settings and no subprocess.
2. A tiny `_env_upsert(path, key, value)` helper to write the `.env` keys
   (idempotent line replace/append).

### CLI

Add to `bin/cli/cluster.sh`: "Set up this node as hub (guided)" and "… as agent
(guided)", each calling `setup_cluster --role …`.

## Out of scope (YAGNI)

`setup_instance`, the role-aware main menu, consolidating the other entrypoints,
and any multi-tier/relay setup.

## Testing

- `setup_cluster`: writes `.env` keys; hub mints a key + reports accepting; agent
  verify success and each failure class (`send_to_hub` mocked); `--no-verify`.
- `push_to_hub`: unchanged behavior after the extraction (existing tests green;
  add direct tests for `build_cluster_payload` / `send_to_hub`).
- 100% branch coverage on changed lines.

## Caveat

Writing `.env` means a running **hub** needs a restart only if `setup_cluster`
*changes* `API_KEY_AUTH_ENABLED` (default is already on → usually none). The
**agent** path needs no restart (`push_to_hub` is its own process).
