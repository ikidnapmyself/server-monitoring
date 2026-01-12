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

- Endpoints must live under `apps/intelligence/views/` (endpoint/module-based).
  - Example: `views/recommendations.py`, `views/health.py`, `views/providers.py`
- Tests must live under `apps/intelligence/tests/` and mirror the module tree.
  - Example: `providers/local.py` → `tests/providers/test_local.py`
  - Example: `views/recommendations.py` → `tests/views/test_recommendations.py`

## Doc vs code status

Some code still uses `views.py` / `tests.py`. This doc defines the **target layout** going forward.
