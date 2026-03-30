---
title: "Security Check Script Design"
parent: Plans
---

# Security Check Script Design

**Date:** 2026-03-30
**Status:** Approved

## Goal

Create `bin/check_security.sh` — a shell script that audits the security posture of a deployment. Auto-detects whether the node is an agent, hub, or standalone instance and runs the relevant checks. Reports PASS/WARN/FAIL with remediation hints.

## Context

The project supports a mixed topology: some agents are on the same network as the hub, others push across the internet. There is no formal ISO certification target — the goal is a solid security baseline that would pass a reasonable audit.

Existing security measures:
- HMAC signing for agent-to-hub push (`X-Cluster-Signature`, SHA-256)
- Webhook signature verification for all inbound drivers
- CI pipeline runs bandit, pip-audit, detect-secrets, trivy

What's missing: a runtime script that checks whether a specific deployment is configured securely.

## Approach

**Pure shell script** (Approach A). Sources existing `bin/lib/` helpers (colors, dotenv, paths). No Python dependency required — can run on a bare node before Django is set up.

## Mode Detection

- **Agent mode:** `HUB_URL` is set
- **Hub mode:** `CLUSTER_ENABLED=1`
- **Standalone:** Neither set (single-instance, gets common checks only)

A node can be both agent and hub simultaneously.

## Checks

### Common (all modes)

| # | Check | Level | Verifies |
|---|-------|-------|----------|
| 1 | `DJANGO_SECRET_KEY` set and >= 50 chars | FAIL | Not empty, sufficient entropy |
| 2 | `DJANGO_DEBUG=0` in non-dev | FAIL | Production not running debug mode |
| 3 | `.env` file permissions | WARN | Not world-readable (600 or 640) |
| 4 | `ALLOWED_HOSTS` is not `*` | WARN | Explicit host allowlist |
| 5 | Python dependencies audit | WARN | Runs pip-audit if available |

### Agent mode (HUB_URL set)

| # | Check | Level | Verifies |
|---|-------|-------|----------|
| 6 | `HUB_URL` uses `https://` | FAIL | TLS for agent-to-hub transport |
| 7 | `WEBHOOK_SECRET_CLUSTER` set and >= 32 chars | FAIL | Strong HMAC secret |
| 8 | Hub reachable | WARN | TCP connectivity to HUB_URL |
| 9 | TLS certificate valid | WARN | Cert validity via curl |

### Hub mode (CLUSTER_ENABLED=1)

| # | Check | Level | Verifies |
|---|-------|-------|----------|
| 10 | `WEBHOOK_SECRET_CLUSTER` set and >= 32 chars | FAIL | Hub can verify agent signatures |
| 11 | Listening port not exposed publicly | WARN | Bound to 0.0.0.0 vs 127.0.0.1 |
| 12 | Reverse proxy detected | WARN | nginx/caddy process running |
| 13 | HTTPS termination | WARN | Behind TLS-terminating proxy |

## Output Format

```
[PASS] HMAC signing configured for agent->hub communication
[WARN] TLS: HUB_URL uses HTTP, not HTTPS — Fix: use https:// in HUB_URL
[FAIL] DJANGO_SECRET_KEY is empty — Fix: generate with python -c "..."
```

## Exit Codes

- `0` — all pass
- `1` — warnings only
- `2` — failures present

## Flags

- `--json` — JSON output (consistent with check_system.sh)
- `--help` — usage info

## Non-Goals

- Auto-fixing issues (report + suggest only)
- Formal ISO 27001 compliance mapping
- Network scanning or port enumeration