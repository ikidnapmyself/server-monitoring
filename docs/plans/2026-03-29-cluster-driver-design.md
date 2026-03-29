---
title: "Cluster Alert Driver"
parent: Plans
---

# Cluster Alert Driver — Design

**Date:** 2026-03-29

## Problem

The app runs as a single instance. There's no way to deploy it across multiple servers where each monitors locally and reports to a central hub. All 8 existing alert drivers are for external tools (Grafana, AlertManager, etc.) — none speak the system's own protocol.

## Goal

Create a 9th alert driver ("cluster") that enables multi-instance deployment. Agent instances run checkers locally, create alerts, and push them to a hub instance via the existing webhook endpoint. The same codebase runs in any role — standalone, agent, hub, or relay — determined purely by environment variables.

## Configuration

Two env vars control behavior (both optional, both unset by default):

| Env var | Default | Purpose |
|---------|---------|---------|
| `HUB_URL` | (unset) | If set, `push_to_hub` sends alerts to this URL |
| `CLUSTER_ENABLED` | `0` | If `1`, cluster driver is registered, hub accepts agent payloads |
| `WEBHOOK_SECRET_CLUSTER` | (unset) | HMAC-SHA256 shared secret for signing |
| `INSTANCE_ID` | `socket.gethostname()` | Human-readable instance identifier |

**Behavior matrix:**

| HUB_URL | CLUSTER_ENABLED | Role |
|---------|----------------|------|
| unset | 0 | **Standalone** — current behavior, zero changes |
| set | 0 | **Agent** — runs checkers, pushes to hub |
| unset | 1 | **Hub** — accepts cluster payloads from agents |
| set | 1 | **Hub + Agent** — accepts payloads AND runs `push_to_hub` (each role is independent) |

Existing installs with neither var set behave exactly as today. Zero breaking changes.

> **Note:** Transparent relay forwarding (automatically re-POSTing received cluster payloads upstream) is not implemented in this version. Each instance is configured independently via its own `HUB_URL`.

## Cluster Alert Driver

New `ClusterDriver` at `apps/alerts/drivers/cluster.py`, registered as `"cluster"` in the driver registry (conditionally, when `CLUSTER_ENABLED=1`).

**Payload format** (what agents POST to `HUB_URL/alerts/webhook/cluster/`):

```json
{
    "source": "cluster",
    "instance_id": "web-server-03",
    "hostname": "ip-10-0-1-42",
    "version": "1.0",
    "alerts": [
        {
            "fingerprint": "cpu-check-ip-10-0-1-42",
            "name": "CPU usage critical",
            "status": "firing",
            "severity": "critical",
            "started_at": "2026-03-29T12:00:00Z",
            "labels": {
                "checker": "cpu",
                "hostname": "ip-10-0-1-42",
                "instance_id": "web-server-03"
            },
            "annotations": {
                "message": "CPU at 95.2%"
            },
            "metrics": {
                "cpu_percent": 95.2
            }
        }
    ]
}
```

**Driver behavior:**
- `validate()` — checks for `source == "cluster"` and `instance_id` field
- `parse()` — maps payload to `ParsedPayload` (same normalized structure as every driver)
- `signature_header` = `"X-Cluster-Signature"` — HMAC-SHA256 using `WEBHOOK_SECRET_CLUSTER`
- Agent hostname and instance_id in `labels` on every alert — hub identifies which server
- `metrics` preserved in `annotations` for intelligence/analysis

Hub treats these alerts identically to Grafana/AlertManager — same deduplication, incident grouping, pipeline flow.

## `push_to_hub` Management Command

New command at `apps/alerts/management/commands/push_to_hub.py`.

**Flow:**
1. Run all enabled checkers locally (reuses existing checker infrastructure)
2. Format results as cluster driver payload
3. Sign with `WEBHOOK_SECRET_CLUSTER` (HMAC-SHA256)
4. POST to `HUB_URL/alerts/webhook/cluster/`
5. Report success/failure

> **Audit trail:** Local check results are not persisted as `Alert` records. The hub creates incident and alert records when it processes the incoming cluster payload via the normal pipeline.

**Flags:**
- `--dry-run` — run checkers, show payload, don't POST
- `--json` — JSON output (for cron logging)
- `--checkers cpu,memory` — specific checkers only

**Error handling:**
- Hub unreachable → log error, exit 1, local alerts preserved
- Hub returns 4xx/5xx → log response, exit 1
- No `HUB_URL` → error message, exit 1

**Cron integration:** Users schedule `push_to_hub` instead of (or alongside) `run_pipeline --checks-only`. No changes to `setup_cron.sh` needed.

## File Changes

**New files:**
- `apps/alerts/drivers/cluster.py` — ClusterDriver
- `apps/alerts/management/commands/push_to_hub.py` — management command
- `apps/alerts/_tests/drivers/test_cluster.py` — driver unit tests
- `apps/alerts/_tests/commands/test_push_to_hub.py` — command tests

**Modified files:**
- `apps/alerts/drivers/__init__.py` — register `"cluster"` conditionally on `CLUSTER_ENABLED`
- `.env.sample` — add cluster section (commented out)
- `docs/Deployment.md` — add multi-instance section
- `config/settings.py` — read `CLUSTER_ENABLED`, `HUB_URL`, `INSTANCE_ID`

**Unaffected:** All existing drivers, install.sh, bin/ scripts, existing tests.

## Approach

Reuse existing webhook infrastructure — the cluster driver is "just another driver." No new endpoints, no new middleware, no new auth. The `push_to_hub` command is the agent-side glue that ties checkers to the webhook POST.

Rejected alternatives:
- Dedicated `/alerts/cluster/` endpoint — unnecessary, existing webhook handles it
- Shared Celery broker — couples instances at infrastructure level, harder to deploy
- Peer-to-peer mesh — over-engineered for the use case