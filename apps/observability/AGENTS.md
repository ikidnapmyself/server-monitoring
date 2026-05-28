# apps.observability — Agent Notes

App-local guidance for working in `apps/observability/`.

## Role

Cross-cutting support module — not a pipeline stage. Provides the substrate the four pipeline stages share: structured JSON-line logging (with `ContextVar` enrichment), heartbeats, the `read_logs` CLI reader, and the cluster log-forwarding registry that lets multiple hosts aggregate logs into a topology of the operator's choosing.

The pipeline stages (`alerts`, `checkers`, `intelligence`, `notify`) call into observability; observability never reaches back.

## Key modules

- `formatter.py` — `JsonLineFormatter` (the canonical wire format) and `PrettyConsoleFormatter` (TTY-only).
- `context.py` — `ContextVar`-backed `trace_id` / `run_id` / `incident_id` / `stage` / `source` enrichment.
- `heartbeat.py` + `heartbeat_reader.py` + `heartbeat_registry.py` — write side, read side, and the registry of declared heartbeat names.
- `log_reader.py` — backs `manage.py read_logs`.
- `models.py` — `ClusterDestination` (cluster log-push registry; see the Cluster topology section).
- `management/commands/` — `read_logs`, `check_heartbeats`, and the `cluster_dest_*` family.

## Cluster topology

Authoritative design: [`docs/plans/2026-05-25-observability-cluster-topology-design.md`](../../docs/plans/2026-05-25-observability-cluster-topology-design.md). Implementation plan: [`docs/plans/2026-05-25-observability-cluster-topology-impl.md`](../../docs/plans/2026-05-25-observability-cluster-topology-impl.md).

The cluster layer lets any combination of hosts form an arbitrary log-aggregation topology — star, fan-in with redundancy, regional aggregator, mesh — defined entirely by per-host `ClusterDestination` rows. The system supplies push, receive, optional forwarding, and a loop-safe identity scheme; the operator wires the graph.

### Record contract (additions to the JSONL schema)

`JsonLineFormatter` stamps three identity fields on every emitted record:

| Field | Type | Set by | Purpose |
|---|---|---|---|
| `record_id` | uuid4 string | `JsonLineFormatter.format` (last, after extras) | Globally unique identity. Powers receiver-side dedup. |
| `instance_id` | string | `JsonLineFormatter.format` (from `settings.INSTANCE_ID` or `socket.gethostname()`) | Origin host. **Never rewritten** by forwarders. |
| `path` | list[string] | Empty `[]` at emit; each forwarder appends its own `instance_id` before pushing | Forwarding chain. Drives the loop-prevention invariant. |

Both `record_id` and `path` are reserved in `_RESERVED_RECORD_KEYS` and stamped *after* the extras pass, so a logger call cannot spoof them via `extra={"record_id": ...}`. There is a regression test for this; do not rearrange the formatter to stamp them earlier.

`instance_id` is preserved as-is by every hop. Forwarders only ever append to `path` — they never rewrite `instance_id` or `record_id`.

### Loop-prevention invariant

A host with `instance_id = X` will never forward a record to any destination whose `api_key.owner_instance_id = X`. `APIKey.owner_instance_id` defaults to `APIKey.name` (see `config/models.py:save`); operators only need to set it explicitly when the key's human label differs from the destination host's instance id.

The push side (PR 2) implements this as: before pushing record `R` to destination `D`, skip if `D.api_key.owner_instance_id in R.path`. The receive side (PR 2) implements a parallel cycle-back check: drop any record whose `path` already contains *this* host's `instance_id` (we forwarded it, it came back).

### `cluster_dest_*` commands

All commands accept `--json` for machine-readable output. Each has a matching interactive entry in `bin/cli/cluster.sh`. The CLI is the primary operator surface; admin is a secondary view.

| Command | Purpose |
|---|---|
| `cluster_dest_add --name --url --api-key [--streams] [--forward]` | Register a new outbound destination. |
| `cluster_dest_list` | Tabular list of all destinations. |
| `cluster_dest_show <name>` | One row in detail, plus recent-pushes pane (empty until PR 2). |
| `cluster_dest_remove --name [--hard]` | Default soft-delete (`is_active=False`); `--hard` drops the row. |
| `cluster_dest_toggle --name` | Flip `is_active`. |
| `cluster_dest_forward --name {on\|off}` | Flip `forward_received`. |
| `cluster_dest_doctor <name>` | One-shot DNS → TCP → TLS → HEAD `/cluster/logs/health/` probe. Exits 1 on any failure. |

Shared helper: `_cluster_dest_common.get_destination_or_raise(name, *, select_api_key=False)` — the canonical lookup. Use it from any new `cluster_dest_*` command instead of re-implementing the `DoesNotExist → CommandError` mapping.

### Troubleshooting checklist

1. **"Can't reach the hub from this host."** Run `manage.py cluster_dest_doctor <name>`. It walks DNS → TCP → TLS → HEAD `/cluster/logs/health/` in that order, stopping at the first failure with a focused error. `--json` is parseable by scripts.
2. **Auth rejected (HTTP 401/403).** The destination's `APIKey` is either inactive, missing the right `allowed_endpoints` entry for `/cluster/logs/`, or was issued for a different host.
3. **HTTP 404 from doctor.** Expected on any host where PR 2 hasn't landed yet — the `/cluster/logs/health/` endpoint ships with the receive side. Doctor labels this case explicitly.
4. **Records not being pushed.** Verify `is_active=True` (`cluster_dest_show <name>`), then check `last_push_at` and `last_push_status`. PR 2 lands the actual push loop.
5. **Suspicious forwarding loops.** Check `record.path` in the JSONL output: if you see the same `instance_id` appearing twice, the loop-prevention invariant has been bypassed somewhere — file a bug with the record id.

### PR phasing

This is a 3-PR series. Each PR's scope is locked at the contract above:

| PR | Lands | Status |
|---|---|---|
| **PR 1** | Model, migration, record contract (`record_id` / `path`), `APIKey.owner_instance_id`, the seven `cluster_dest_*` commands, admin registration, the `bin/cli/cluster.sh` menu. **Configuration plane only — no pushing, no receiving.** | Current branch |
| **PR 2** | `push_logs_to_hub` command + cron entry, `POST /cluster/logs/<stream>/` receive view with dedup LRU + cycle-back check, per-destination heartbeats, `cluster_status` command, `O004` system check. | Future |
| **PR 3** | `forward_received=True` semantics end-to-end, multi-hop loop-prevention tests (direct cycle, 3-node cycle, diamond, forwarded-to-self), `O005` hub-side freshness check. | Future |

Items 8 (Cluster status) and 9 (Push logs now) in `bin/cli/cluster.sh` are stubs in PR 1 that print "lands in PR 2" — leave them stubs until PR 2 actually ships, do not silently wire them up halfway.

## Admin expectations

Each app must provide an extensive `admin.py`. For `apps.observability`, admin should make it easy to:

- Inspect `ClusterDestination` rows with `name`, `hub_url`, `is_active`, `forward_received`, `last_push_at`, `last_push_status` in `list_display`.
- Filter by `is_active` and `forward_received`.
- Search by `name` and `hub_url`.
- Read recent heartbeats and structured logs through the read-side helpers (heartbeat reader / log reader).

No custom admin actions in PR 1 — every operation has a CLI counterpart that is the primary path. Admin is the secondary "operations surface" required by the project rule.

## App layout rules (required)

- Endpoints (once PR 2 lands `/cluster/logs/<stream>/`) must live under `apps/observability/views/` as one module per endpoint.
- Tests live under `apps/observability/_tests/` and mirror the source tree. Management-command tests live under `_tests/management/`.
- Fixtures and shared helpers belong in `apps/observability/_tests/conftest.py` or `_tests/_helpers/`.

## Boundary rules

- Stage code logs through standard Python logging; it never imports `JsonLineFormatter` directly. Formatter wiring is the responsibility of `LOGGING` in `config/settings.py`.
- The cluster push loop (PR 2) is read-only against `LOGS_DIR/{events,heartbeats}.jsonl` — it never mutates other stages' log output. It writes only its own cursor files under `LOGS_DIR/cluster_push_cursor/` and (when receiving) under `LOGS_DIR/cluster/<source_instance_id>/`.
- Outbound HTTP from this app **must** go through `config.security.http.safe_urlopen` with `allowed_hosts=settings.SSRF_ALLOWED_HOSTS`. Raw `urllib.request.urlopen` is banned by ruff `TID251`.
- Do not log `APIKey.key` or any raw token. Log `APIKey.name` and `prefix` only.