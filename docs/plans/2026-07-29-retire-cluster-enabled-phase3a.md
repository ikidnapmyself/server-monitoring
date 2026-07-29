---
title: "Phase 3a: Retire CLUSTER_ENABLED + conflict role — Implementation Plan"
parent: Plans
---

# Phase 3a: Retire CLUSTER_ENABLED + Conflict Role

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the vestigial `CLUSTER_ENABLED` flag and the "conflict" role. A node's role is derived from `HUB_URL` (agent) and active API keys + auth (receiving/hub); a node can legitimately be both (`agent+hub`).

**Architecture:** `CLUSTER_ENABLED` never gated ingest (the API key is the sole gate) — it only drove role display and preflight. The peer-node design (`docs/plans/2026-07-26-node-model-control-plane-design.md`) retires it: "am I a hub?" = "do I have active keys and am I receiving?". So `get_profile()` derives role from `HUB_URL` + a small `APIKey` query (`doctor` already does this), the "conflict" error is deleted (`agent+hub` is valid), and `CLUSTER_ENABLED` is removed from settings, the installer, security checks, `.env.sample`, and docs.

**Tech Stack:** Django 5.2, pytest + pytest-django, bats, uv.

**Conventions (from AGENTS.md):** absolute imports; 100% branch coverage on changed lines; `uv run pytest`, `uv run black .`, `uv run ruff check .`, bats via `./bin/tests/test_helper/bats-core/bin/bats`. Commit per task. TDD throughout.

**Non-historical `CLUSTER_ENABLED` must end at zero hits** (`git grep CLUSTER_ENABLED -- ':!docs/plans/*'`) except where a bats/grep test asserts its *removal*.

---

## Task 1: Role derived from HUB_URL + active keys (no conflict)

**Files:**
- Modify: `apps/checkers/preflight/dashboard.py` (`get_profile`)
- Test: `apps/checkers/_tests/preflight/test_dashboard.py`

**Context:** `get_profile()` currently derives role from `HUB_URL` + `CLUSTER_ENABLED`, producing `standalone|agent|hub|conflict`. Replace `CLUSTER_ENABLED` with a "receiving" signal (has an active `APIKey` **and** `API_KEY_AUTH_ENABLED`). A node with both `HUB_URL` and receiving is `agent+hub` (no more conflict). This adds a DB query to `get_profile`, so its tests must be `TestCase` (DB), not `SimpleTestCase`.

**Step 1: Update the failing tests** in `test_dashboard.py`:
- The class must be a `django.test.TestCase` (needs DB). Read the file; if `GetProfileTests` is `SimpleTestCase`, change it and its imports.
- `test_hub_profile`: instead of `CLUSTER_ENABLED=True`, set `@override_settings(API_KEY_AUTH_ENABLED=True)`, create an active `APIKey` (`from config.models import APIKey; APIKey.objects.create(name="agent-x")`), leave `HUB_URL=""`, assert `profile["role"] == "hub"` and `profile["receiving"] is True`.
- Replace `test_conflict_profile` with `test_agent_and_hub_profile`: `@override_settings(HUB_URL="https://h", API_KEY_AUTH_ENABLED=True)` + an active `APIKey`; assert `profile["role"] == "agent+hub"`.
- `test_agent_profile`: `HUB_URL="https://h"`, no keys / auth off → assert `role == "agent"`, `receiving is False`.
- `test_standalone_profile`: no `HUB_URL`, no active keys → `role == "standalone"`.
- Add `test_receiving_requires_auth_enabled`: an active `APIKey` but `@override_settings(API_KEY_AUTH_ENABLED=False)` → `role == "standalone"`, `receiving is False` (covers the auth-off branch).

**Step 2: Run** `uv run pytest apps/checkers/_tests/preflight/test_dashboard.py -v` — expect FAILs.

**Step 3: Implement** — replace the role block in `get_profile`:

```python
def _is_receiving() -> bool:
    """A node accepts pushes (is a hub) when auth is on and it has an active key."""
    from config.models import APIKey

    if not getattr(settings, "API_KEY_AUTH_ENABLED", False):
        return False
    return APIKey.objects.filter(is_active=True).exists()


def get_profile() -> dict:
    """Build a system profile dict from Django settings and the API-key state."""
    hub_url = getattr(settings, "HUB_URL", "")
    is_agent = bool(hub_url)
    receiving = _is_receiving()

    if is_agent and receiving:
        role = "agent+hub"
    elif is_agent:
        role = "agent"
    elif receiving:
        role = "hub"
    else:
        role = "standalone"
    ...
```

Keep the rest of the returned dict; **add** `"receiving": receiving`, and **remove** the `cluster_enabled` line. Add `from config.models import APIKey` at module top only if you prefer (a local import inside `_is_receiving` avoids import-time coupling — prefer the local import).

**Step 4: Run** the dashboard tests — PASS.

**Step 5: Commit**
```bash
git add apps/checkers/preflight/dashboard.py apps/checkers/_tests/preflight/test_dashboard.py
git commit -m "feat(preflight): derive role from HUB_URL + active keys; retire conflict"
```

---

## Task 2: `check_cluster_coherence` — delete conflict, base hub on keys

**Files:**
- Modify: `apps/checkers/preflight/checks.py` (`check_cluster_coherence`)
- Test: `apps/checkers/_tests/preflight/test_checks.py`

**Context:** It currently reads `CLUSTER_ENABLED`, returns an `error` when both `HUB_URL` and `CLUSTER_ENABLED` are set ("Cluster conflict…"), and has a hub branch keyed on `CLUSTER_ENABLED`. Delete the conflict error entirely. Keep the agent warnings (empty `HUB_API_KEY` / `INSTANCE_ID` when `HUB_URL` is set). Replace the hub branch to key off "receiving" (reuse `_is_receiving()` from dashboard, or `APIKey` + `API_KEY_AUTH_ENABLED` inline).

**Step 1: Update tests** in `test_checks.py` `CheckClusterCoherenceTests` — remove the `test_agent_and_hub_conflict` (or repurpose it to assert **no error** when both are set), drop `CLUSTER_ENABLED=...` overrides, and express "hub" via an active `APIKey` + `API_KEY_AUTH_ENABLED=True`. Keep the agent-mode warning tests.

**Step 2: Run** — expect FAILs.

**Step 3: Implement** — rewrite `check_cluster_coherence` without `CLUSTER_ENABLED`:
- Delete the `hub_url and cluster_enabled` conflict block.
- Agent (`hub_url` set): warn if `HUB_API_KEY` empty; warn if `INSTANCE_ID` empty.
- Hub (receiving): informational "Hub mode: accepting authenticated pushes (N active key(s))".
- If nothing else: `ok` "Cluster: <role>" where role comes from `get_profile()["role"]` (single source) or the agent/receiving booleans.

**Step 4: Run** — PASS.

**Step 5: Commit**
```bash
git add apps/checkers/preflight/checks.py apps/checkers/_tests/preflight/test_checks.py
git commit -m "feat(preflight): cluster coherence keyed on API keys, not CLUSTER_ENABLED; no conflict"
```

---

## Task 3: Remove `CLUSTER_ENABLED` from settings (+ any readers)

**Files:**
- Modify: `config/settings.py` (delete the `CLUSTER_ENABLED` line + its comment)
- Grep + fix any remaining Python reader.

**Step 1:** Delete `config/settings.py:213` (`CLUSTER_ENABLED = ...`) and adjust the comment above it to describe the key-based hub model.

**Step 2:** `git grep -n "CLUSTER_ENABLED" -- '*.py' ':!*_tests*' ':!docs/plans/*'` — there should be **no** remaining Python readers (Tasks 1–2 removed `preflight`/`dashboard`). Fix any stragglers.

**Step 3:** Run `uv run pytest apps/checkers/_tests/ -q` and grep the test tree for `CLUSTER_ENABLED=` overrides that are now dead; remove/replace them (some may exist in `test_command.py`).

**Step 4: Commit**
```bash
git add config/settings.py apps/checkers/_tests
git commit -m "refactor(config): remove dead CLUSTER_ENABLED setting"
```

---

## Task 4: Installer + security-check shell (drop CLUSTER_ENABLED)

**Files:**
- Modify: `bin/install/cluster.sh` (remove the `CLUSTER_ENABLED=1` write in hub/both branch — keep `API_KEY_AUTH_ENABLED=1` + the `create_api_key` guidance)
- Modify: `bin/lib/security_check.sh` (role detection at ~line 105 and the `run_agent_checks`/`run_hub_checks` dispatch)
- Test: `bin/tests/test_cluster.bats` (extend), and `bin/tests/` for security-check role selection if covered

**Step 1: Update/add bats** — `test_cluster.bats` should assert `cluster.sh` **no longer writes `CLUSTER_ENABLED`**, and still references `HUB_API_KEY` + `create_api_key`. For `security_check.sh`, assert it selects hub checks from `API_KEY_AUTH_ENABLED` (not `CLUSTER_ENABLED`) — add a focused test if the harness supports it; otherwise a `grep` assertion that `CLUSTER_ENABLED` is gone.

**Step 2: Run** — FAIL.

**Step 3: Implement**
- `cluster.sh` hub/both branch: remove `dotenv_set "$_ENV_FILE" "CLUSTER_ENABLED" "1"`; keep `API_KEY_AUTH_ENABLED=1` + the "mint a key with create_api_key" guidance. Update the header comment (`# Configures: …`) to drop `CLUSTER_ENABLED`.
- `security_check.sh`: replace the `cluster_enabled=$(_sc_env_val "CLUSTER_ENABLED")` role logic. Run **agent** checks when `HUB_URL` is set; run **hub** checks when `API_KEY_AUTH_ENABLED` = `1` (a node may run both). Remove the `CLUSTER_ENABLED` read.

**Step 4: Run** `./bin/tests/test_helper/bats-core/bin/bats bin/tests/*.bats bin/tests/lib/*.bats` — PASS, and `bash -n` the two scripts.

**Step 5: Commit**
```bash
git add bin/install/cluster.sh bin/lib/security_check.sh bin/tests
git commit -m "feat(installer): hub role = auth on + minted key; drop CLUSTER_ENABLED"
```

---

## Task 5: Docs + `.env.sample`

**Files:**
- Modify: `.env.sample` (cluster block), `docs/Deployment.md`, `bin/README.md`

**Step 1:**
- `.env.sample`: remove `# CLUSTER_ENABLED=0` from the cluster block; reword so hub = "set `API_KEY_AUTH_ENABLED=1` and create an APIKey via `create_api_key`" (no `CLUSTER_ENABLED`).
- `docs/Deployment.md`: replace `CLUSTER_ENABLED=1` in the hub setup with "create an API key + `API_KEY_AUTH_ENABLED=1`"; remove `CLUSTER_ENABLED` from the env table if present; fix any "conflict" wording.
- `bin/README.md`: drop `CLUSTER_ENABLED` references.

**Step 2: Gate** — `git grep -n "CLUSTER_ENABLED" -- ':!docs/plans/*'` returns only the bats/grep tests that assert its removal. Everything else clean.

**Step 3: Commit**
```bash
git add .env.sample docs/Deployment.md bin/README.md
git commit -m "docs: cluster hub is key-based; remove CLUSTER_ENABLED"
```

---

## Task 6: Full verification

```bash
uv run black . --check
uv run ruff check .
uv run pytest -q
uv run coverage run -m pytest && uv run coverage report   # 100% on changed lines
./bin/tests/test_helper/bats-core/bin/bats bin/tests/*.bats bin/tests/lib/*.bats
git grep -n "CLUSTER_ENABLED" -- ':!docs/plans/*'   # only removal-asserting tests remain
uv run python manage.py doctor                       # role shows agent/hub/agent+hub/standalone
```

Then finish via `@superpowers:finishing-a-development-branch`.

---

## Notes for the executor

- **Behavior is unchanged for ingest** — `CLUSTER_ENABLED` never gated the webhook; this is a role-display/config cleanup. Do not add any new ingest gate.
- **Single source of role:** `get_profile()["role"]`. `check_cluster_coherence` and `doctor` should not re-derive role differently.
- **`get_profile` now hits the DB** (APIKey). Its tests must be `TestCase`. Watch for other callers assuming it is DB-free.
- **Coverage:** the `agent+hub` / `hub` / auth-off branches in role derivation are the easy misses — all are covered by the Task 1 tests.
