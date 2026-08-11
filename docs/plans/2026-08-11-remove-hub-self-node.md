# Remove Hub Self-Node — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Delete the inert hub self-node concept (from PR #202): `Node.is_self`, `Node.ensure_self()`, `bootstrap_self_node`, and the self-node branch in `_resolve_node`.

**Architecture:** Pure deletion. Keep `PipelineRun.node` + the `origin` enum; only the *self-node* mechanism goes. After removal, `_resolve_node` has a single label-based path and hub `checker_generated` runs resolve to `node=None` (already the production behavior, since `ensure_self()` no-ops when `INSTANCE_ID` is empty).

**Tech Stack:** Django ORM + migrations, pytest. `uv run` for everything.

**Conventions (AGENTS.md):** absolute imports; 100-char lines; Black + Ruff; 100% branch coverage on changed code; `bandit` clean. Design: `docs/plans/2026-08-11-remove-hub-self-node-design.md`.

**Full reference inventory (verified):**
- `apps/alerts/models.py`: `is_self` field (~340-342), `ensure_self()` (~383-393), `import socket` (line 5 — used ONLY by ensure_self).
- `apps/alerts/admin.py:549-550`: `is_self` in `list_display` + `list_filter=["is_self"]`.
- `apps/alerts/management/commands/bootstrap_self_node.py` (+ `apps/alerts/_tests/management/test_bootstrap_self_node.py`).
- `apps/alerts/_tests/test_models.py:63-90`: 5 tests (`test_node_is_self_defaults_false`, `test_ensure_self_creates_self_node`, `test_ensure_self_is_idempotent`, `test_ensure_self_noop_when_instance_id_unset`).
- `apps/alerts/_tests/test_admin.py:10-12`: `test_nodeadmin_shows_is_self`.
- `apps/orchestration/orchestrator.py`: `_resolve_node(payload, origin)` self-node branch (~202-203) + docstring (~189-198); call site `self._resolve_node(payload, origin)` (~166).
- `apps/orchestration/_tests/test_orchestrator.py:670-680`: `test_checker_generated_run_gets_self_node`.
- Help-text mentions of "hub self-node": `apps/alerts/models.py` (is_self help_text, removed with the field) and `apps/orchestration/models.py:108` (`PipelineRun.node` help_text) + `apps/orchestration/migrations/0006_*.py:20` (historical migration — do NOT edit).
- `apps/alerts/AGENTS.md`: self-node documentation paragraph (added in #202).

---

## Task 1: Remove `Node.is_self` + `ensure_self()` + migration

**Files:**
- Modify: `apps/alerts/models.py`
- Create: `apps/alerts/migrations/0009_remove_node_is_self.py` (auto-generated)
- Modify: `apps/alerts/_tests/test_models.py`

**Step 1: Delete the obsolete tests first (they define the behavior we're removing).**
In `apps/alerts/_tests/test_models.py`, delete the 4 tests at lines ~63-90 (`test_node_is_self_defaults_false`, `test_ensure_self_creates_self_node`, `test_ensure_self_is_idempotent`, `test_ensure_self_noop_when_instance_id_unset`). Keep all other tests (the Alert/Incident model tests, Node.upsert tests). Remove any now-unused imports (e.g. `override_settings`) only if nothing else uses them.

**Step 2: Remove the field + method from the model.**
In `apps/alerts/models.py`:
- Delete the `is_self = models.BooleanField(...)` field.
- Delete the entire `ensure_self` classmethod.
- Remove `import socket` (line 5) — verify with `grep -n socket apps/alerts/models.py` that no other usage remains; only remove if grep shows none.

**Step 3: Generate the migration.**

Run: `uv run python manage.py makemigrations alerts`
Expected: creates `0009_remove_node_is_self.py` with a single `RemoveField(model_name="node", name="is_self")`.

**Step 4: Verify.**

Run: `uv run pytest apps/alerts/_tests/test_models.py -q`
Expected: PASS (no is_self/ensure_self references remain).
Run: `uv run python manage.py makemigrations --check --dry-run` → No changes detected.

**Step 5: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations/0009_remove_node_is_self.py apps/alerts/_tests/test_models.py
git commit -m "refactor(alerts): remove Node.is_self and ensure_self()"
```

## Task 2: Remove `bootstrap_self_node` command + admin flag

**Files:**
- Delete: `apps/alerts/management/commands/bootstrap_self_node.py`
- Delete: `apps/alerts/_tests/management/test_bootstrap_self_node.py`
- Modify: `apps/alerts/admin.py`
- Modify: `apps/alerts/_tests/test_admin.py`

**Step 1: Delete the command and its test.**

```bash
git rm apps/alerts/management/commands/bootstrap_self_node.py \
       apps/alerts/_tests/management/test_bootstrap_self_node.py
```
If `apps/alerts/_tests/management/` is left with only `__init__.py` and no other tests, leave the `__init__.py` (harmless) — do not remove the package.

**Step 2: Remove `is_self` from NodeAdmin.**
In `apps/alerts/admin.py`:
- Remove `"is_self"` from `NodeAdmin.list_display` (line ~549).
- Remove the `list_filter = ["is_self"]` line entirely (line ~550) — `is_self` was its only entry; NodeAdmin had no other list_filter before #202.

**Step 3: Remove the admin test.**
In `apps/alerts/_tests/test_admin.py`, delete `test_nodeadmin_shows_is_self` (lines ~10-12). If that leaves an unused `NodeAdmin` import, remove it only if nothing else in the file uses it.

**Step 4: Verify.**

Run: `uv run pytest apps/alerts/ -q`
Expected: PASS.
Run: `uv run python manage.py check` (ensure no missing-command / admin errors).

**Step 5: Commit**

```bash
git add -A apps/alerts/
git commit -m "refactor(alerts): drop bootstrap_self_node command and is_self admin column"
```

## Task 3: Simplify `_resolve_node` (single resolution path)

**Files:**
- Modify: `apps/orchestration/orchestrator.py`
- Modify: `apps/orchestration/_tests/test_orchestrator.py`
- Modify: `apps/orchestration/models.py` (help_text wording)

**Step 1: Flip the orchestrator test first.**
In `apps/orchestration/_tests/test_orchestrator.py`, replace `test_checker_generated_run_gets_self_node` (~670-680) with:

```python
@pytest.mark.django_db
def test_checker_generated_run_has_null_node():
    run = PipelineOrchestrator().start_pipeline(
        payload={"checks_only": True},
        source="cli",
        origin=PipelineOrigin.CHECKER_GENERATED,
    )
    assert run.origin == PipelineOrigin.CHECKER_GENERATED
    assert run.node is None
```
(Drop the `@override_settings(INSTANCE_ID="hub-1")` decorator — no longer relevant.)

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_orchestrator.py::test_checker_generated_run_has_null_node -q`
Expected: FAIL — currently returns the self-node (or None only because INSTANCE_ID unset in tests; the decorator removal + assertion change should still fail against the ensure_self branch if INSTANCE_ID is set by other config — if it unexpectedly passes, that's fine, but proceed to Step 3 to remove the branch regardless).

**Step 3: Remove the self-node branch + `origin` param.**
In `apps/orchestration/orchestrator.py` `_resolve_node`:
- Delete the `if origin == PipelineOrigin.CHECKER_GENERATED: return Node.ensure_self()` branch.
- Since `origin` is now unused inside `_resolve_node`, remove the `origin` parameter from its signature and update the call site at ~line 166 from `self._resolve_node(payload, origin)` to `self._resolve_node(payload)`.
- Update the docstring to describe the single label-based path for all origins (remove the "CHECKER_GENERATED runs concern the hub itself…" sentence).
- Remove the now-unused `from apps.alerts.models import Node` import inside `_resolve_node` only if `Node` is no longer referenced there (the `Node.objects.filter(...)` lookup likely still uses it — keep if so).

**Step 4: Fix help_text wording.**
In `apps/orchestration/models.py:108`, change the `PipelineRun.node` help_text from "Server this run concerns (agent node, or the hub self-node)." to "Server this run concerns (the agent node resolved from the alert's instance_id)." Do NOT touch the historical migration `0006_*.py`.

**Step 5: Verify.**

Run: `uv run pytest apps/orchestration/ -q`
Expected: PASS (including the flipped test).
Run: `uv run python manage.py makemigrations --check --dry-run` → No changes (help_text on a non-schema… note: help_text changes DO trigger a migration in Django. If `makemigrations --check` reports a change, generate it: `uv run python manage.py makemigrations orchestration` — it will be an `AlterField` with only help_text. Include it in the commit.)

**Step 6: Commit**

```bash
git add apps/orchestration/
git commit -m "refactor(orchestration): single node-resolution path, drop self-node branch"
```

## Task 4: Docs + final gate

**Files:**
- Modify: `apps/alerts/AGENTS.md`

**Step 1: Update docs.**
In `apps/alerts/AGENTS.md`, remove/rewrite the self-node paragraph added in #202 so it no longer claims `Node.is_self` / `ensure_self()` / `bootstrap_self_node` exist. Keep the rest of the Node description accurate (agent registry keyed by instance_id).

**Step 2: Full gate.**

Run:
```bash
grep -rn "is_self\|ensure_self\|bootstrap_self_node" apps/ config/ bin/   # expect: no matches
uv run coverage run -m pytest && uv run coverage report
uv run black . --check
uv run ruff check .
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py makemigrations --check --dry-run
```
Expected: no self-node references remain; full suite green; 100% branch coverage on changed lines; lint/format/bandit clean; no uncommitted migrations.

**Step 3: Commit**

```bash
git add apps/alerts/AGENTS.md
git commit -m "docs(alerts): drop self-node references"
```

## Acceptance criteria

- `grep -rn "is_self\|ensure_self\|bootstrap_self_node" apps/ config/ bin/` returns nothing.
- `Node` has no `is_self` column (migration `0009` applied); `_resolve_node` takes only `payload` and has one resolution path.
- Full CI green; coverage 100% on changed lines; deployed alert-grouping behavior unchanged.
