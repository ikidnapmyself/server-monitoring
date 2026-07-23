---
title: "Unified Auth + Cluster-as-Driver — Design"
parent: Plans
---

# Unified Auth + Cluster-as-Driver — Design

**Status:** Design, written 2026-07-23. Approved, pending implementation plan.

This is **Slice A** of a two-part effort. Slice B — source→pipeline routing and
merging the two orchestrators — is a separate brainstorm that follows immediately after
A and will supersede A's interim checker-skipping rule. A is deliberately self-contained:
it fixes authentication, makes cluster an ordinary driver, and corrects one wrong-behavior
cheaply, **without** building the router.

## Problem

Three complaints, one root cause:

1. **Multiple authentication surfaces make everything complex.** There are three, and two
   collide.
2. **Cluster is not treated as an alert driver** — it is one, but special-cased.
3. **Too many moving parts to set up a cluster**, including a config knob that does nothing.

### What the code actually does (findings)

**Authentication — three surfaces, two of which stack on the same request:**

- **API-key middleware** (`config/middleware/api_key_auth.py`) — `Authorization: Bearer` /
  `X-API-Key` resolved against the DB `APIKey` model. Gates *all* non-GET requests under
  `/alerts/`, `/orchestration/`, `/notify/`, `/intelligence/`. Global on/off via
  `API_KEY_AUTH_ENABLED`. `EXEMPT_PATH_PREFIXES` is only `/admin/` and `/static/`; the
  health-check exemption is GET-only.
- **Per-driver HMAC** (`apps/alerts/views.py:60`) — each driver may declare a
  `signature_header`; the view looks up `WEBHOOK_SECRET_<DRIVER>` from the environment and
  verifies `HMAC-SHA256(body, secret)`. Only enforced when that secret is set.
- **Admin session** for `/admin/`.

The API-key layer and the HMAC layer are independent and unaware of each other, so a
cluster agent sending a valid HMAC signature still receives **401** from the API-key layer
when `API_KEY_AUTH_ENABLED=1`. That is the `config.W002` trap: the "fix" (enable auth)
breaks cluster ingestion.

**The per-driver HMAC is a scaffold, not working vendor auth.** `base.verify_signature`
is a single generic `HMAC-SHA256(body, secret)` (with optional `sha256=` prefix
stripping); **no driver overrides it**. It does not match how real vendors sign — e.g.
PagerDuty v3 sends `X-PagerDuty-Signature: v1=<hex>`, and this code strips only `sha256=`,
so a genuine PagerDuty signature would fail. `X-Grafana-Signature` / `X-NewRelic-Signature`
are not headers those products send. Only `WEBHOOK_SECRET_CLUSTER` is defined anywhere; the
other `WEBHOOK_SECRET_<DRIVER>` vars are never wired. **The only signature that ever fires
is cluster's**, and only because we control both ends.

**Cluster is a conditionally-registered driver.** `ClusterDriver` is added to
`DRIVER_REGISTRY` only when `CLUSTER_ENABLED=1` (`apps/alerts/drivers/__init__.py:48`).
The other eight are always registered. `setup_instance` builds its driver list from
`DRIVER_REGISTRY`, so cluster is invisible in the wizard purely because of that gate.

**`CLUSTER_ROLE` is dead config.** The installer writes it; nothing in the app reads it.
Role is derived from `HUB_URL` + `CLUSTER_ENABLED` in `get_profile()`.

**No source routing exists (context for B, not fixed in A).** Two orchestrators live side
by side. The webhook path uses `PipelineOrchestrator`, whose stage selection is hardcoded
(`orchestrator.py:262`): `checks_only` in the payload runs CHECK-only, otherwise the full
`STAGE_ORDER`. `source` is only recorded for tracing, never used to route.
`DefinitionBasedOrchestrator` runs a `PipelineDefinition` but is handed the definition by
name and is not used by the webhook path. `PipelineDefinition` has no `source`/`match`
field. Consequence: a sibling node's cluster push runs the hub's *own* checkers against
*itself* — wasteful and semantically wrong. A applies a minimal, driver-declarative fix;
B replaces it with real routing.

## Scope A — design

### 1. Unified authentication

- The API-key middleware becomes the **single authentication gate** for all entrypoints.
  One credential type: an API key sent as `Authorization: Bearer <key>`, backed by the
  existing `APIKey` model (hashed at rest). Admin keeps Django session auth.
- `push_to_hub` sends `Authorization: Bearer <HUB_API_KEY>` and **drops HMAC signing**.
- The per-driver HMAC scaffold (`signature_header`, `verify_signature`, dynamic
  `WEBHOOK_SECRET_<DRIVER>` lookup) **remains in the tree, dormant** — the documented
  extension point for a future vendor driver that overrides `verify_signature` with that
  vendor's real scheme. It is no longer relied upon by any shipped path.
- Because agents now carry a real API key, `API_KEY_AUTH_ENABLED=1` is safe in production
  and `config.W002` is resolved properly rather than suppressed.

### 2. Cluster as a normal driver

- Delete the conditional registration (`_register_cluster_driver` and its `CLUSTER_ENABLED`
  gate). `ClusterDriver` joins `DRIVER_REGISTRY` unconditionally, appears in the
  `setup_instance` wizard, and is authenticated by API key like every other driver. Its
  only distinct trait is its payload format (`source=cluster`, per-instance check results).

### 3. Minimal semantic fix — cluster skips checkers (driver-declarative)

- Add `skip_checkers: bool = False` to `BaseAlertDriver`. `ClusterDriver` sets it `True`:
  a sibling's push already carries its own diagnostics, so the hub must not re-run local
  checks against itself.
- The webhook enqueue path reads the resolved driver's `skip_checkers` and drops the CHECK
  stage for that run, yielding **Alert → Intelligence → Notify** for cluster while other
  sources keep the full pipeline.
- This is a property of the *driver*, not a hardcoded source-string branch in the
  orchestrator — explicitly the anti-pattern B removes. It is marked in code as the interim
  rule that B's router generalizes.

### 4. Provisioning — `manage.py create_api_key`

- New management command mints an `APIKey` and prints the raw token **once** (the model
  already generates it into the transient `_raw_key`):
  `manage.py create_api_key --name "agent web-03"`.
- The installer's cluster step wires it: hub role runs the command and shows the token;
  agent role prompts for it and stores `HUB_API_KEY`.

### 5. Config cleanup

- Delete dead `CLUSTER_ROLE`.
- Agent config becomes `HUB_URL`, `INSTANCE_ID`, `HUB_API_KEY`. `WEBHOOK_SECRET_CLUSTER`
  is retired outright (no dual-path fallback — coordinated cutover across the 8 nodes).
- **Keep `CLUSTER_ENABLED`**, but only as the hub-intent/display flag for `get_profile()`
  role and preflight — decoupled from driver registration. Its ultimate fate is B's call
  once routing exists.

Config surface, before → after:

| | Before | After |
|---|---|---|
| Agent | `HUB_URL`, `INSTANCE_ID`, `WEBHOOK_SECRET_CLUSTER`, `CLUSTER_ROLE` | `HUB_URL`, `INSTANCE_ID`, `HUB_API_KEY` |
| Hub | `CLUSTER_ENABLED`, `WEBHOOK_SECRET_CLUSTER`, `CLUSTER_ROLE` | `CLUSTER_ENABLED` + one `APIKey` record |

### 6. Defaults & security posture

- `.env.sample` flips `API_KEY_AUTH_ENABLED` from `0` to `1` (security-first default;
  settings already default to `1`). A fresh dev install then needs a key for API POSTs;
  health and webhook-less dev are unaffected.
- `set_production.sh` ensures `API_KEY_AUTH_ENABLED=1`.

## Migration (the 8 nodes)

1. Deploy A to the hub; create a key: `manage.py create_api_key --name "<agent>"`.
2. Set `HUB_API_KEY` on each agent's `.env`; remove `WEBHOOK_SECRET_CLUSTER` and
   `CLUSTER_ROLE`.
3. Ensure `API_KEY_AUTH_ENABLED=1` on the hub.
4. Verify with `push_to_hub --dry-run` then a live push.

Coordinated cutover; documented as a short migration note in the deployment docs.

## Testing

- **Middleware:** webhook POST returns 401 without a key and 200 with a valid key.
- **`push_to_hub`:** sends the Bearer header and no signature; errors clearly when
  `HUB_API_KEY` is unset.
- **Registration:** `ClusterDriver` is present in `DRIVER_REGISTRY` unconditionally and
  appears in the wizard's driver list.
- **`skip_checkers`:** a cluster-driver run omits the CHECK stage; a non-cluster run keeps
  it.
- **`create_api_key`:** mints a key, prints the raw token exactly once, persists only the
  hash.
- **Preflight:** cluster checks updated (no `WEBHOOK_SECRET_CLUSTER`; `HUB_API_KEY` for
  agents).
- Full branch coverage on changed code.

## Acceptance criteria

1. With `API_KEY_AUTH_ENABLED=1`, a cluster agent authenticated by `HUB_API_KEY` pushes
   successfully; an unauthenticated push is 401. `config.W002` no longer fires in a
   configured production instance.
2. `ClusterDriver` is always registered and selectable in `setup_instance`.
3. A cluster push runs Alert → Intelligence → Notify (no hub checkers); a Grafana webhook
   runs the full pipeline.
4. `manage.py create_api_key` exists and prints a usable token once.
5. `CLUSTER_ROLE` and `WEBHOOK_SECRET_CLUSTER` are removed from code, `.env.sample`, and
   the installer; `HUB_API_KEY` replaces them.
6. `uv run pytest`, `pip-audit --strict`, and the bats suite are green; branch coverage on
   changed code is 100%.

## Explicitly deferred to B

- Source→pipeline routing (`source`/driver/cron-origin → a `PipelineDefinition`).
- Merging `PipelineOrchestrator` and `DefinitionBasedOrchestrator` into one engine so the
  webhook path runs definitions.
- The final disposition of `CLUSTER_ENABLED` once routing makes "hub-ness" a routing
  question rather than a flag.
- Per-alert checker selection (finer than stage on/off; A's `skip_checkers` is binary).
