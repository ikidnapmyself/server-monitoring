---
title: "2026-05-31 Observability Over-build — Post-mortem"
parent: Plans
---

# Observability Over-build — Post-mortem

**Status:** Retrospective, written 2026-05-31, after the observability stack (#156) was
reverted on `main` (#161) and the cluster-topology layer (#159) was deprecated.

This is a **blameless** post-mortem. It examines a system and process, not people. The
goal is to name what we'd do differently so the next feature stays small.

## Summary

- **Attempted:** an "observability stack" — structured JSON logging, heartbeats, and a
  log-reader CLI (#154 design → #155 implementation plan → #156 feature) — followed by a
  cluster log-aggregation topology on top of it (#159).
- **Reverted:** #156 was removed in full (#161), restoring the pre-observability state.
- **Deprecated before merge:** #159 (the cluster topology) was closed; its branch never
  reached `main`. See `docs/plans/2026-05-30-cluster-topology-deprecation-design.md`.
- **Kept:** the existing node→hub mechanism (`push_to_hub` + inbound `ClusterDriver` +
  HMAC webhook) and the `notify` stage. These were always sufficient.

## Timeline

| Date | Ref | Event |
|------|-----|-------|
| 2026-05-17 | #154 | Design doc — observability stack (629 lines) |
| 2026-05-18 | #155 | Implementation plan (4,051 lines) |
| 2026-05-25 | #156 | Feature merged — 50 files, +3,740 / −544 |
| 2026-05-25 | #159 | Cluster-topology effort opened (mesh, dedup, `/cluster/logs/`) |
| 2026-05-30 | — | Cluster topology reviewed, judged YAGNI, deprecated; #159 closed |
| 2026-05-31 | #161 | Observability stack (#156) reverted in full |

## Central finding: we reinvented transport we already had

The most important lesson is not generic "scope creep." It is specific:

**The project already had a clean, idiomatic, single-hop node→hub mechanism, and the new
work neither found it nor reused it.**

What already existed:

- **`push_to_hub`** (`apps/alerts/management/commands/push_to_hub.py`) — a node runs its
  own checkers on a cron, builds a faithful payload (status, severity, timestamps, labels,
  metrics, `instance_id`), **HMAC-SHA256-signs** it (`X-Cluster-Signature`,
  `WEBHOOK_SECRET_CLUSTER`), and POSTs it through the SSRF-protected `safe_urlopen` to the
  hub. Single hop.
- **`ClusterDriver`** (`apps/alerts/drivers/cluster.py`) — simply the 9th alert driver. It
  inherits `BaseAlertDriver` and ingests cluster payloads over the **same
  `/alerts/webhook/` path every other driver uses** (alertmanager, datadog, grafana,
  pagerduty, …).
- **The `notify` stage** — the per-node "IFTTT" surface for ad-hoc reporting, composing
  with the existing notification drivers.

What #159 built instead: a parallel universe — a `ClusterDestination` model, a
`cluster_dest_*` CLI family, `record_id`/`path[]` on every log line,
`APIKey.owner_instance_id`, a new `POST /cluster/logs/<stream>/` endpoint with per-host
API-key auth, an LRU dedup cache, and a four-scenario mesh loop-prevention scheme.

It duplicated transport, auth, and ingestion that the Driver pattern + webhook already
provided — and then added mesh machinery for a topology nobody has. The intended use case
was modest: **each node monitors itself and reports to one central hub.** That is
single-hop fan-in, which the existing channel already does.

## Root causes (systemic)

1. **No capability inventory before building.** The node→hub channel existed and was
   idiomatic. The effort built a new one rather than discovering and extending the old.
2. **The established pattern was bypassed, not extended.** The repo has a strong, uniform
   Driver/Provider pattern over one webhook path. The cluster layer introduced a parallel
   endpoint, a separate auth scheme, and a destination registry instead of being "just
   another driver / the existing one." Fighting the pattern was a signal that went unheard.
3. **We solved a topology we don't have.** Mesh, multi-hop, dedup, and loop-prevention only
   earn their cost in arbitrary topologies. Single-hop fan-in has no cycles and no
   duplicates, so all of that machinery was cost without benefit.
4. **A cross-cutting concern was miscategorized as an app.** Structured logging is a
   cross-cutting utility: it belongs in logging *configuration* + a thin formatter, consumed
   through stdlib `logging`, which every app already uses. Built as `apps/observability`, it
   was instead imported across the pipeline (`orchestrator.py`, `push_to_hub`, the checkers
   logging path). That inverted coupling — everyone depending on the app — is exactly why
   removing it was a 50-file operation. A "low-dependency app" was structurally
   high-dependency.
5. **Plan volume was mistaken for rigor.** A 4,051-line implementation plan for what should
   be a small feature was a scope warning treated as thoroughness. Big planning artifacts
   created an illusion of diligence while enabling, not constraining, the over-build.

A note on review: the obviously-excessive layer (the cluster mesh, #159) tripped the YAGNI
alarm and was caught before merge. The *plausible* layer (the base stack, #156) passed
review and only got reverted later. Plausible over-build is more dangerous than egregious
over-build, precisely because it survives review.

## What went right

- **Review caught the worst of it.** #159 was stopped before reaching `main`; the mesh code
  never shipped.
- **The existing mechanism is sound and was kept.** `push_to_hub` + `ClusterDriver` + the
  HMAC webhook were correctly identified as sufficient and retained unchanged.
- **The revert was clean and verified.** #156 was reverted to a byte-exact pre-feature
  state with the full test suite green (2,089 passing).

## Lessons → guardrails

These lessons are encoded as enforceable rules in `AGENTS.md` → **"Scope discipline —
avoid over-build"**:

1. **Inventory before building** — name existing capability before adding a new mechanism.
2. **Respect the established pattern** — reuse the Driver/Provider + webhook path; friction
   with the pattern is a stop signal.
3. **App vs. utility test** — cross-cutting concerns live in shared utilities/config, not an
   app others import.
4. **Solve the topology you have** — no mesh/dedup/scale machinery without a current
   requirement; oversized plans are a smell, not rigor.

## Deferred

The question of whether (and how) to reintroduce structured logging — as a small,
decoupled, cross-cutting utility rather than an app — is intentionally left open and will be
decided separately. This post-mortem does not redesign it.