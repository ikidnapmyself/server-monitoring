---
title: "2026-05-30 Cluster Topology — Deprecation Decision"
parent: Plans
---
# Cluster Topology — Deprecation Decision

**Status:** Decided 2026-05-30. Supersedes the observability cluster log-aggregation
topology effort (PR #159 and the three-PR plan it opened).

## Decision

We will **not** build the observability cluster topology — multi-destination log
forwarding, mesh / multi-hop, receiver-side dedup, and `path[]` loop-prevention —
nor a dedicated forwarding driver. PR #159 is closed and its branch discarded; none
of that code reaches `main`.

Node→hub reporting is already covered by existing, merged capability, and that is
what we rely on:

- **`push_to_hub`** (`apps/alerts/management/commands/push_to_hub.py`) — a node runs
  its own checkers on a cron and POSTs the results to a hub's
  `POST /alerts/webhook/cluster/`, signed with HMAC-SHA256
  (`X-Cluster-Signature`, secret `WEBHOOK_SECRET_CLUSTER`). Single hop, and faithful:
  it carries status, severity, timestamps, labels, metrics, and `instance_id`.
- **Inbound `ClusterDriver`** (`apps/alerts/drivers/cluster.py`) — ingests those
  payloads into the hub's incident list, so one host becomes the single pane of glass.
- **The pipeline + `notify` stage** — the polymorphic, per-node "IFTTT" reporting
  surface. Each node configures its own `NotificationChannel` rows and pipeline
  definitions: *if* this alert fires, *then* run this pipeline, whose notify stage
  fans out to Slack, PagerDuty, email, or a Generic webhook (including another
  instance). The operator wires whatever shape they want from existing primitives.

## Context

A prior effort (`docs/plans/2026-05-25-observability-cluster-topology-design.md` and
`-impl.md`, both preserved in closed PR #159) proposed a new cluster log-aggregation
layer in `apps/observability`: a `ClusterDestination` model, a `cluster_dest_*` CLI
family, `record_id` / `path[]` fields on every log record, `APIKey.owner_instance_id`,
a new `POST /cluster/logs/<stream>/` receive endpoint with per-host API-key auth, an
LRU dedup cache, and a four-scenario loop-prevention scheme for arbitrary mesh
topologies.

On review this was judged to be solving problems we do not have. The intended use
case is modest: **each node monitors itself (via cron) and reports to one central
hub so everything is watchable in one place.** That is single-hop fan-in, which the
existing channel already does.

## Rationale

- **Duplicated transport.** The proposal reinvented a node→hub channel (push, receive,
  auth, a destination registry) when `push_to_hub` + the inbound `ClusterDriver` +
  the HMAC webhook already provide exactly that.
- **YAGNI.** Mesh, multi-hop forwarding, dedup, and loop-prevention only earn their
  cost in arbitrary topologies. Single-hop fan-in has no cycles to break and no
  duplicates to suppress.
- **Smaller security surface.** A parallel `/cluster/logs/` endpoint, per-host API
  keys, and a DB-stored destination registry add attack surface and secret-handling
  for no current benefit. Keeping one signed channel is easier to reason about.
- **The pipeline is already the IFTTT engine.** Reporting belongs in the `notify`
  stage, where it is per-node configurable and composes with the other notification
  drivers — not in a bespoke side-channel.

## Scope — what is deprecated

All of the following lived only on the `feat/observability-cluster-topology` branch
and never reached `main`; they are abandoned with the closed PR #159:

- The `ClusterDestination` model, its migration, and admin registration.
- The `cluster_dest_*` management commands (`add`, `list`, `show`, `remove`, `toggle`,
  `forward`, `doctor`) and the `bin/cli/cluster.sh` destination menu.
- The `record_id` and `path[]` fields stamped by `JsonLineFormatter`.
- `APIKey.owner_instance_id`.
- The three-PR plan (`2026-05-25-observability-cluster-topology-*`).
- The proposed `cluster` *notify* driver (considered as an alternative; never built).

## What we keep

- `push_to_hub` (cron-driven, faithful, HMAC-signed, single hop) — unchanged.
- The inbound `ClusterDriver` and `POST /alerts/webhook/cluster/` ingestion path.
- The `notify` stage and its existing drivers (Email, Slack, PagerDuty, Generic) for
  ad-hoc, per-node reporting — including pointing a Generic channel at another
  instance's `/alerts/webhook/`.

## If real log aggregation or mesh is ever needed

Adopt an existing, battle-tested tool rather than building one in-house. Options:
Fluent Bit, Vector, Grafana Loki + Promtail, rsyslog, or the OpenTelemetry Collector.
These already solve batching, backpressure, dedup, multi-hop fan-in, TLS/auth, and
loop avoidance — the exact problems the in-house topology tried to reinvent. Building
that layer in Django would be a maintenance liability with no differentiation.

## Deferred decision

When such a tool is actually adopted, revisit (and only then):

- Whether `push_to_hub` is retained, replaced, or made pipeline-native (run inside a
  scheduled pipeline's notify stage rather than as a standalone cron command).
- Whether the inbound `ClusterDriver` remains the hub's ingestion path or is
  superseded by the adopted tool's own ingestion.

These are intentionally left open. Settling them before there is a concrete
aggregation requirement would be premature.

## Consequences

- The `cluster_dest_doctor` review fixes and the rest of the PR-1 code are abandoned
  with the branch (preserved for the record in closed PR #159).
- `apps/observability` gains no cluster-forwarding surface; its role stays
  "structured logging, heartbeats, log reader."
- Operators wanting central monitoring use the existing `push_to_hub` + cluster
  webhook path; per-node ad-hoc reporting uses the `notify` stage.