---
title: "Peer Node Model + Unified Control Plane — Architecture"
parent: Plans
---

# Peer Node Model + Unified Control Plane — Architecture

**Status:** Design, written 2026-07-26. Approved (foundation). Phased build to follow.

This design records the foundational architecture for turning the project into a
**plug-and-play server monitoring app** — one that is genuinely useful **without
any paid SaaS or hosted AI**, and simple enough that clustering is as close to
plug-and-play as the single-node case already is. It supersedes the implicit
role-enforcement model and the two-orchestrator confusion.

## Vision (the design contract)

Create a plug-and-play server monitoring app that does **not require expensive
SaaS or hosted AI**, yet is **good enough to democratize server management**.

This is a constraint on the architecture, not a feature:

> **The core loop — `check → alert → notify` — must be fully valuable with zero
> paid services and zero hosted AI.** Everything else is an *optional enhancer*
> that may improve it but must never be required for it.

Free ≠ dumb: `apps/intelligence/providers/local.py` (`LocalRecommendationProvider`)
is a rule-based engine that already produces real remediation advice (e.g. for
`disk_linux` / `disk_macos`) with no external API. **Local intelligence is the
free default enricher; hosted AI is an optional upgrade.**

## Core decisions (foundational — expensive to reverse)

### 1. Peer + credentials — roles are descriptive, not enforced

Stop modeling "role" as an exclusive enum (`standalone|agent|hub|conflict`).
Model behaviour as composable, **derived** facts. Primitives:

- **Every node runs checks on itself.**
- **Every node exposes exactly one authenticated ingest endpoint**
  (`POST /alerts/webhook/cluster/`).
- **A node may push its results to one or more peers** it holds credentials for.
- **A node stores what it receives** in a first-class `Node` registry.
- **A node may aggregate, route, and notify.**

"Hub" and "agent" become **labels for how a node is used**, shown to humans and
derived from config + observed traffic. They are never validated or gated. This
dissolves "multiple roles", the "conflict" state, and role-enforcement
complexity — a node simply does whatever its config + credentials allow
("roles added brain-free").

### 2. The API key is the single ingest gate

There is **no separate `accept_incoming` flag and no `CLUSTER_ENABLED` gate.**

> A node accepts a push **iff** the caller presents a valid, active API key that
> permits `/alerts/webhook/cluster/`. Mint a key → the node is a receiver.
> Revoke/delete the keys → ingest is closed (401 for everyone). Default state (no
> keys) = closed.

The key both **authorizes and scopes** (folding in `APIKey.allowed_endpoints`).
The only invariant the diagnostics enforce: **`API_KEY_AUTH_ENABLED` stays on**
(the secure default we already ship), so the key is never bypassed. This retires
`CLUSTER_ENABLED` as an ingest control; "am I a hub?" is derived from "do I have
active keys and am I receiving?".

### 3. The `Node` registry is the data spine

Today cluster pushes land as loose `Alert`s tagged with an `instance_id` string;
there is **no first-class node/agent record**. Introduce a `Node` model
(identity / `instance_id`, hostname, address, last-seen, and links to its
results / incidents). **Cluster ingest upserts a `Node` on every accepted push**,
so a receiver actually *knows* its peers. This one model is the load-bearing
"store agent and hub data perfectly" decision, and it is the **queryable record**
that the future report/view API reads. Cheap to get right now, expensive to
retrofit.

### 4. Optionality contract — degrade gracefully, no paid dependency

Each dependency is an **optional enhancer** with graceful degradation; the core
works with none of them:

- **Intelligence:** local rule-based provider is the default; hosted AI optional.
- **Notification content is sourced from the alert/check data itself** (what
  failed, which checker, severity, host, `trace_id`). **Intelligence *enriches*
  when present — it is never the producer and never a gate.** (Today
  `NotifyExecutor` builds the message *from* intelligence recommendations, so no
  AI ⇒ empty notifications. That coupling is cut.)
- **Celery/Redis:** optional; the existing synchronous fallback stays.
- **Vendors:** email / generic webhook are the zero-cost defaults; Slack /
  PagerDuty are optional.

### 5. Unified control plane — commands are the API, surfaces are projections

- **Management commands are the single implementation** (the "verbs"). The CLI
  menu, installer, admin, and `cron_setup` are **thin clients** that call them —
  no parallel shell-vs-Python logic. The CLI menu is **generated from / validated
  against the command set + node state**, so it cannot drift or show broken /
  irrelevant items ("entrypoints uniformed and unified").
- **`preflight` ≡ `doctor`:** one diagnostic authority that reads *node config ×
  registry × per-capability requirements* and reports what is missing + verifies
  end-to-end. No two separate diagnostic systems.
- **`cron_setup` wires the scheduled pipeline / push** as part of setup.
- **One unit, few commands:** a small verb set (e.g. `setup`, `doctor`, `check`,
  `push`, `serve`) rather than the current sprawl of overlapping entrypoints
  (`install.sh cluster`, `setup_instance`, `create_api_key`, CLI menus, admin,
  raw `.env`).

## What this retires or corrects

- `CLUSTER_ENABLED` as an ingest gate, the `accept_incoming` flag, the "conflict"
  role, and enforced role identity — **all gone**; role is derived.
- The misleading `checkers.I001` "0 pipeline definitions" signal for the webhook
  path: the cluster/webhook pipeline runs the definition-free
  `PipelineOrchestrator` and **never needs a `PipelineDefinition`**. Definitions
  become an optional, de-surfaced enhancer.
- Notify's dependency on hosted AI for content (see §4).

## Deferred — allowed by this model, deliberately **not** built now

Per the repo's over-build post-mortem, we do not build multi-hop machinery
without a live requirement. The spine (`Node` + `Alert` + `Incident`) leaves the
door open for:

- **Alert → pipeline routing** (aggregate related alerts, route to a chosen
  pipeline). The router was intentionally dropped earlier; revisit only with a
  concrete need.
- **Multi-tier forwarding** (a middle node propagating a child's alert upstream).
  Cycle-free composition is allowed; forwarding/dedup/loop machinery is not built.
- **API extraction for report / view apps** — read models over the `Node` spine.
- Richer aggregation, notification templating, admin config pages, guided-setup
  polish.

## Phased build

- **Phase 1 — Data spine + aligned diagnostics (mostly non-breaking).** `Node`
  registry model + upsert on ingest; `doctor` unified with `preflight`; make the
  API key the sole ingest gate (retire `CLUSTER_ENABLED` gating).
- **Phase 2 — Optionality / notify decoupling.** Notification content from
  alert/incident data; local intelligence as default enricher; verify the full
  loop works with no paid AI/SaaS.
- **Phase 3 — Unified entrypoints.** Commands-as-API consolidation; role-aware
  generated CLI menu; `cron_setup` wires the pipeline; guided `setup as hub/agent`;
  de-surface definitions and fix the misleading warning.

Each phase is independently shippable and leaves the system working.

## Success criteria

1. A fresh node monitors itself and sends a **useful Slack/email alert with no AI
   and no paid service** configured.
2. Making a node a receiver is **mint a key**; disabling it is **revoke the key** —
   no other toggle.
3. `doctor` on any node reports exactly which of the required pieces are missing
   and verifies the cluster path end-to-end.
4. The CLI menu shows only actions valid for the node's current state; nothing
   broken or irrelevant.
5. A hub has a first-class, queryable record of every agent that has pushed to it.
6. No enforced roles, no `CLUSTER_ENABLED`/`accept_incoming` gate; behaviour is
   derived from keys + config + traffic.
