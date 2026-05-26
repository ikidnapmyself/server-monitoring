---
title: "2026-05-25 Observability Cluster Topology"
parent: Plans
---
# Observability Cluster Topology

## Context

PR #156 shipped the local observability stack — JSON line logging, ContextVar enrichment, heartbeats, read_logs CLI. The original observability design (`docs/plans/2026-05-17-observability-stack-design.md`, Section 6) sketched an agent→hub log forwarding layer but explicitly punted **cluster topology** — "fan-in vs mesh, agent-as-hub configurability, multi-hop forwarding, identity / discovery" — to a separate brainstorm. This is that brainstorm.

The project is steering toward a CLI-first operator experience. `bin/cli.sh` and its submenus (`bin/cli/cluster.sh`, etc.) are the primary operator surface; Django admin remains a secondary "operations surface" that satisfies the project rule that every app must have substantive admin. The cluster design is shaped accordingly: every operator workflow is reachable from `bin/cli.sh > cluster > …`, and admin gets the same model rows for free via Django auto-admin.

## Goal

Let any combination of monitored hosts form an arbitrary log-aggregation topology — star, fan-in with redundancy, regional hub-of-hubs, mesh, anything — defined entirely by per-host configuration. No prescribed roles; roles emerge from configuration. The system supplies the primitives (push, receive, store, optional forward, loop-safe identity), the operator wires the graph.

## Non-goals

- A discovery / gossip layer (operators define destinations explicitly).
- A pull-based architecture (push only — matches the existing `push_to_hub` pattern for alerts).
- A metrics pipeline (this is log shipping, not a metrics stack).
- A web log viewer (CLI reader + admin are sufficient).
- TLS mutual auth between hubs (the existing APIKey mechanism is enough; mTLS is a follow-up).
- Wire compression of push payloads (gzip transport is a small, isolated follow-up).

## Approach

Definition-driven, multi-destination, forwarding-capable from day one. The data model expresses any topology shape; most deployments will exercise only a small subset of the capabilities. The contract (record identity + forwarding chain + loop break) lives in the JSONL record format, so a flat deployment today can grow into a multi-hop one tomorrow without a format migration.

### Data model

One new model in `apps.observability`:

```python
class ClusterDestination(models.Model):
    name = models.CharField(max_length=64, unique=True)              # admin-friendly identifier
    hub_url = models.URLField()                                      # https://hub.example.com
    api_key = models.ForeignKey("alerts.APIKey", on_delete=PROTECT)  # auth credential held locally
    streams = models.CharField(max_length=128, default="events,heartbeats")
    forward_received = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    max_batch_bytes = models.PositiveIntegerField(default=10 * 1024 * 1024)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_push_status = models.CharField(max_length=32, blank=True)   # "ok", "fail:401", "fail:5xx", ...
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

One row per outbound destination this host knows about. Hub-only hosts have zero rows (they receive but don't push). Agents have one or more. Default deployment is a single row per host pointing at the central hub.

Soft delete via `is_active=False`; `--hard` flag on the remove command actually drops the row.

### Record contract (additions to the JSONL schema)

Two new fields on every emitted record, added by `JsonLineFormatter`:

| Field | Type | Set by | Purpose |
|---|---|---|---|
| `record_id` | uuid4 string | Formatter at emit time | Globally unique identity. Powers dedup at receivers and across re-paths. |
| `instance_id` | string | Formatter (already exists, from `OBSERVABILITY_INSTANCE_ID`) | Origin host. **Never rewritten** by forwarders. |
| `path` | list[string] | Empty at emit; each forwarder appends its own `instance_id` before pushing | Forwarding chain. Used for loop prevention. |

`instance_id` is preserved as-is by every hop; only `path` grows. `record_id` is stable across hops (the same record being re-forwarded keeps its uuid).

### Push semantics

`manage.py push_logs_to_hub` (cron-invoked + CLI-exposed):

1. Read each active `ClusterDestination` row.
2. For each destination, maintain a cursor at `LOGS_DIR/cluster_push_cursor/<destination_name>.json` recording the last successfully-acked file offset per stream.
3. Read new records since the cursor from local `LOGS_DIR/{events,heartbeats}.jsonl`. If `forward_received=True`, also read from `LOGS_DIR/cluster/<source_instance_id>/{events,heartbeats}.jsonl` (records forwarded to us by other agents).
4. For each record, check loop conditions: if the destination's `api_key.owner_instance_id` (see Identity section) is already in `record.path`, skip; otherwise append local `instance_id` to `path` and queue for push.
5. POST to `{hub_url}/cluster/logs/<stream>/` with the destination's `api_key` as auth. Batch up to `max_batch_bytes`.
6. On `2xx { accepted_bytes }` response, advance cursor. On 4xx, log redacted body + emit `heartbeat("cluster_push.<name>", status="fail")`; cursor untouched. On 5xx / timeout, retry next tick; cursor untouched.
7. Failure on one destination does not affect pushes to others (each destination's loop body is isolated).

Per-destination heartbeat `cluster_push.<destination_name>` is auto-registered when the destination row is created and emitted per successful push tick.

### Receive semantics

`POST /cluster/logs/<stream>/` (existing URL pattern from Section 6 of the original design):

1. Authenticate via `APIKey` middleware (already in place for the alerts cluster driver).
2. Authorize via `allowed_endpoints` containing `/cluster/logs/` (already enforced).
3. Validate the body: JSONL, well-formed, each record has `instance_id` and `record_id`.
4. **Dedup check:** consult an LRU of recently-seen `record_id`s (in-process cache, default 100k entries, 1h TTL — configurable via `OBSERVABILITY_DEDUP_CACHE_*`). Drop duplicates silently; increment a counter exposed via admin and `cluster_status` CLI.
5. **Cycle-back check:** if any record's `path` already contains this host's `instance_id`, drop it (we forwarded it earlier and it came back). Increment a separate counter.
6. Append surviving records to `LOGS_DIR/cluster/<source_instance_id>/<stream>.jsonl`, where `source_instance_id` is the record's `instance_id` (origin, not last-hop).
7. Respond `200 { received: N, accepted: M, deduped: N-M }`.

`source_instance_id` is validated against `^[a-z0-9._-]{1,64}$` at write time (same regex as the alerts cluster driver).

### Identity model

`APIKey` gains one optional field: `owner_instance_id` (defaults to `name` for backward compat). This is the `instance_id` of the host the key was issued *to*. Used by the loop check: when pushing to destination D, we know D belongs to `instance_id = D.api_key.owner_instance_id`. If that instance_id appears in `record.path`, that host has already seen the record — skip.

The record's `instance_id` continues to identify the *origin*; `APIKey.owner_instance_id` identifies the *next-hop recipient*. These differ for forwarded records, and that divergence is the whole point.

### CLI surface (primary)

Wraps new `manage.py` commands. All commands accept `--json` for machine-readable output.

| `manage.py` command | `bin/cli/cluster.sh` menu | Purpose |
|---|---|---|
| `cluster_dest add --name --url --api-key --streams [--forward]` | Add destination (interactive prompts) | Create row; validate URL + API key reachability via HEAD probe. |
| `cluster_dest list` | List destinations | Table: name, url, streams, last push, status. |
| `cluster_dest show <name>` | Show destination details | Row + recent 10 push attempts. |
| `cluster_dest remove <name> [--hard]` | Remove destination (confirm) | Soft delete (default) or hard delete (`--hard`). |
| `cluster_dest toggle <name>` | Enable / disable | Flip `is_active`. |
| `cluster_dest forward <name> {on|off}` | Set forward-received policy | Flip `forward_received`. |
| `cluster_dest doctor <name>` | Test destination | One-shot diagnose: DNS, TLS, auth, schema acceptance. |
| `cluster_status` | Cluster status | Per-destination freshness; warnings if any push is stale > `OBSERVABILITY_CLUSTER_MAX_AGE`. |
| `push_logs_to_hub` (existing name; rename or alias) | Push logs now (manual) | What cron runs; CLI exposure for debugging. |

### Admin (secondary)

Django auto-registers `ClusterDestination` with sensible `list_display`, `list_filter`, `search_fields`. No custom admin actions in v1 — every operation has a CLI counterpart that is the primary path.

### System checks

One new check `O004` (numbering continues from `O003` reserved for future use):

- **O004** — for each active `ClusterDestination`, warn if `last_push_at` is older than `OBSERVABILITY_CLUSTER_MAX_AGE` (default 900s, already a setting). Surfaces in `preflight` and admin. Same internal-driver Alert path used by H001/H002/H003.

The existing `check_cluster_freshness` design (which walked `APIKey`s on the hub side to detect missing agents) is folded into a parallel check `O005` — hub-side freshness, looking at `cluster/<instance_id>/heartbeats.jsonl` mtime.

### Bootstrap UX

First-install on a new host:

```
$ bin/cli.sh cluster
> Cluster menu
  1) Add destination
  2) List destinations
  3) Show destination details
  4) Remove destination
  5) Enable / disable destination
  6) Set forward-received policy
  7) Test destination
  8) Cluster status
  9) Push logs now (manual)
  0) Back
> 1
  Destination name (e.g. central-prod): central-prod
  Hub URL: https://hub.example.com
  API key (will be validated against hub): [paste]
  Streams to push [events,heartbeats]:
  Forward records received from other agents? [y/N]: N

  Probing https://hub.example.com... ✓ reachable
  Validating API key... ✓ accepted, owner_instance_id=agent-a
  Created destination 'central-prod'.
  Next push tick in ~60s, or run "Push logs now" from this menu.
```

No env-var bootstrap. No `.env` editing. No restart.

## Topology shapes this enables

All of these are pure configuration — no code changes per shape:

| Shape | Configuration on each node |
|---|---|
| **Standalone** | No destinations. Logs stay local. |
| **Star (fan-in)** | Every agent: one destination = central hub. Central hub: no destinations. |
| **Star with redundancy** | Every agent: two destinations = `hub-a` and `hub-b`. Hubs themselves have no destinations. |
| **Regional aggregator** | Agents push to regional. Regional: one destination = central, with `forward_received=True`. Central: no destinations. |
| **Mesh** | Each host has destinations pointing to all the others it should reach. Loop prevention is structural via `path` + LRU dedup. |
| **Hub-only** | No destinations. Only serves `/cluster/logs/<stream>/`. Receives, never pushes. |

The data model is identical across all shapes. Operators don't pick a "mode" — they pick destinations.

## Rejected alternatives

- **Env-var-only configuration.** Earlier draft proposed `CLUSTER_DESTINATIONS=hub_a=url1;hub_b=url2;…`. Ugly for nested policies (which streams? which API key? forward-received per destination?), no live editing, no admin visibility, conflicts with the CLI-first direction. Dropped.

- **DB-backed model with env-var fallback for bootstrap.** Earlier draft kept env vars for first-install only ("if no rows, read `HUB_URL` env"). Sounds nice; in practice creates two sources of truth, confusing precedence, and reinforces a deployment habit (editing `.env`) the CLI-first direction wants to retire. Replaced by `cli > cluster > add destination` as the explicit first-install step.

- **Pull-based instead of push-based.** Hubs poll agents at `GET /cluster/logs/since/<cursor>/`. Cleaner backpressure model and inverts the firewall direction. Rejected because (a) every agent would need to be reachable from every hub, an enterprise-network headache, and (b) the existing alerts `push_to_hub` already wired the push pattern. Inverting now is a structural rewrite for marginal benefit.

- **Hop-count TTL instead of `path` array.** Add `hops_remaining: int` decremented at each forward. Simpler to implement than `path`, but lets duplicates slip through if topology has multiple paths and TTL exceeds the shortest path. The `path` approach is loop-correct regardless of topology; the storage cost (a small array of short strings) is negligible.

- **Server-side dedup via persistent storage (DB or Redis) instead of in-process LRU.** Persistent dedup would survive process restarts but adds infra. In-process LRU with a 1h TTL is sufficient — duplicates that arrive an hour apart are rare in practice, and the cost of an occasional duplicate (one extra JSONL line in storage) is low compared to the cost of running Redis. Re-evaluate if observed dedup miss rates justify it.

## Risk

Medium. The contract additions (`record_id`, `path`) touch the formatter — a hot path in every emitted record — so the implementation must be defensive against formatter failures (already a project rule). The loop-prevention logic is non-trivial; tests must cover at least: direct cycle (A → B → A), three-node cycle (A → B → C → A), legitimate diamond (X → A → C; X → B → C), and forwarded-to-self (record already has my instance_id in `path`).

Low-risk parts: the data model itself, the CLI surface, the admin page, the per-destination cursor refactor.

Failure mode to call out: if the LRU dedup cache is too small for the deployment's actual record rate, duplicates leak through. The default (100k entries, 1h TTL) handles ~28 records/sec per source; settings-overridable.

## Done criteria

1. `ClusterDestination` model exists, migration applied, admin page registered.
2. All 9 `manage.py` commands listed above implemented with `--json` output and proper exit codes.
3. `bin/cli/cluster.sh` menu wraps every command with prompts and confirmations.
4. `POST /cluster/logs/<stream>/` view implements dedup + cycle-back-check.
5. Loop-prevention contract has tests for the four scenarios above.
6. Per-destination heartbeats auto-register and emit.
7. `O004` and `O005` system checks land in `preflight`.
8. Documentation in `apps/observability/AGENTS.md` covers: adding a destination, the topology contract (`record_id`/`path`), troubleshooting via `cluster_dest doctor`.
9. Bootstrap flow tested end-to-end on a fresh install.
10. Full pytest + pip-audit + bandit green; 100% branch coverage on touched code; all CI checks pass.

## Out of scope for this PR (parked follow-ups)

- TLS mutual auth between hubs.
- gzip wire compression of push payloads.
- Rate limiting on `/cluster/logs/<stream>/` beyond the existing APIKey rate limit.
- A `cluster_dest discover` command that auto-probes for siblings.
- Web UI for cluster status (admin auto-page is sufficient).
- Backpressure / async push (today: synchronous cron tick is enough).
- Compression of cluster-side rotated logs (already separately parked for the local logs).
