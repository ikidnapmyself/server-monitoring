# apps.intelligence — Agent Notes

This file contains **app-local** guidance for working in `apps/intelligence/`.

## Role in the pipeline

Stage: **analyze**

Responsibilities:
- Produce incident analysis + recommended actions based on incident + checker output
- Use a provider-architecture (`apps/intelligence/providers/`)

Output contract (to orchestrator):
- `{ summary, probable_cause, actions, confidence, ai_output_ref, model_info }`

## Key modules

- `apps/intelligence/providers/` — provider implementations and registry
- `apps/intelligence/management/commands/` — `get_recommendations`
- `apps/intelligence/urls.py` — URL routing

## Boundary rules

- **Do not** dispatch notifications or advance stages directly.
  - Only `apps.orchestration` coordinates stage execution.
- Prompts, payloads, and provider outputs must avoid leaking secrets.
  - Store **redacted refs** rather than raw content.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.intelligence`, admin should make it easy to:
- Inspect intelligence outputs for pipeline runs (typically via `apps.orchestration.StageExecution` with `stage="analyze"`)
- See provider/model metadata and token usage when captured
- Correlate analysis with `Incident` and `trace_id/run_id`

## App layout rules (required)

- Endpoints must live under `apps/intelligence/views/` as modules (endpoint/module per file).
  - Examples: `views/recommendations.py`, `views/health.py`, `views/providers.py`
  - Prefer small modules (one view function or class per file) to simplify imports and testing.
- Tests must live under `apps/intelligence/_tests/` and mirror the module tree in `views/` and `providers/`.
  - Examples:
    - `apps/intelligence/providers/local.py` → `apps/intelligence/_tests/providers/test_local.py`
    - `apps/intelligence/views/recommendations.py` → `apps/intelligence/_tests/views/test_recommendations.py`
  - Test files should be discoverable by pytest (use `test_*.py` or `*_tests.py` naming — `pyproject.toml` in the repo supports these patterns).
- Fixtures, shared helpers, and package-level test utilities belong in `apps/intelligence/_tests/conftest.py` or `apps/intelligence/_tests/_helpers/`.

## Doc vs code status

Tests have been migrated to `_tests/` (completed). Some code still uses monolithic `views.py`; migrate to `views/` package when touching related code.
