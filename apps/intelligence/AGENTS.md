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
  - `ai_base.py` — `BaseAIProvider` shared by all LLM-backed providers
  - `local.py` — Local analysis provider (fallback/default)
  - `openai.py`, `claude.py`, `gemini.py`, `copilot.py`, `grok.py`, `ollama.py`, `mistral.py` — LLM providers
  - `__init__.py` — `PROVIDERS` registry, `get_provider()`, `get_active_provider()`
- `apps/intelligence/models.py` — `IntelligenceProvider` (DB-driven config), `AnalysisRun`
- `apps/intelligence/management/commands/` — `get_recommendations`
- `apps/intelligence/urls.py` — URL routing

## Provider selection

- **DB-driven (default)**: The orchestrator calls `get_active_provider()` which queries `IntelligenceProvider`
  for the active DB record. If found, returns a configured provider instance.
- **Explicit override**: If the pipeline payload specifies a `provider` key, `get_provider(name)` is used instead.
- **Fallback**: If no active DB provider exists (or DB is unavailable), falls back to `LocalRecommendationProvider`
- **Config**: Provider credentials stored in `IntelligenceProvider.config` JSONField
- **Single active**: Only one provider can be `is_active=True` at a time (enforced by model + UniqueConstraint)

## Boundary rules

- **Do not** dispatch notifications or advance stages directly.
  - Only `apps.orchestration` coordinates stage execution.
- Prompts, payloads, and provider outputs must avoid leaking secrets.
  - Store **redacted refs** rather than raw content.
- **Always use absolute paths**: Any user-supplied path (HTTP params, CLI args) must be resolved to absolute form via `pathlib.Path.resolve()` and validated against an allowlist before use. See `views/disk.py` for the reference implementation.

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

## Security standards (audit-enforced)

Authoritative source: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/intelligence/` section. This module produced the audit's only MEDIUM finding ([Finding 1](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `scan_paths` config bypass, fixed 2026-05-13). The rules below codify what the fix protects.

### Rules for new provider kwargs

`apps.intelligence.providers.get_provider` / `get_active_provider` accept caller-supplied `**kwargs` from API payloads via `provider_config`. The `BLOCKED_CONFIG_KEYS` frozenset (`apps/intelligence/providers/__init__.py:79`) strips dangerous kwargs before they reach the provider constructor. **Any new kwarg added to a provider's `__init__` requires one of:**

1. **Constructor-time validation** with `validate_safe_url(value, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)` (for URL / host kwargs), `resolve_safe_path(value)` (for path kwargs), or a project-specific validator.
2. **Adding the key to `BLOCKED_CONFIG_KEYS`** so the registry strips it before it can reach the constructor from an API caller. DB-side admin-configured values still flow through.

Current `BLOCKED_CONFIG_KEYS`: `{"host", "base_url", "scan_paths"}`. **Audit checklist for new kwargs:**

| Kwarg accepts… | Required mitigation |
|---|---|
| URL or hostname | `validate_safe_url` at `__init__` (see `ollama.py`, `grok.py`, `copilot.py`); add to `BLOCKED_CONFIG_KEYS` if defaults are server-controlled |
| Filesystem path | `resolve_safe_path` at `__init__` **or** add to `BLOCKED_CONFIG_KEYS` (the `scan_paths` precedent) |
| Shell command / argv | Reject at `__init__`; commands MUST be hardcoded class constants |
| Template name or path | `resolve_safe_name` (or sandbox via `apps/notify/templating.py`) |
| API key / secret | Allowed; keep out of `__init__` log lines and the admin display |

### Other rules
- **`_redact_config` (`apps/intelligence/providers/base.py:208`) must stay in sync with provider config shape.** When you add a new sensitive config key (api keys, tokens, secrets), add it to the redaction list. The `_redact_config` is shallow — flat dicts only; if a provider grows nested config, the redaction must become recursive.
- **LLM output is parsed only by `json.loads`** in `ai_base.py:137`. Never route LLM output into `eval`, `exec`, `subprocess`, or `os.system`. Prompt-injection is treated as an untrusted-content concern, not a code-execution concern, **because** of this rule — keep it that way.
- **Provider dispatch is via the fixed `PROVIDERS` dict.** Do not introduce string-based dynamic import (`importlib.import_module(payload["provider"])`).
- **Path-walking sinks (`Path.rglob`, `os.walk`)** in `LocalRecommendationProvider` follow symlinks by default — acceptable because callers cannot supply `scan_paths` (post-Finding 1 fix). Any code that reads file *contents* from these walks must add per-entry `resolve_safe_path` validation.

### Trust boundary discipline
- `provider_config` from `/intelligence/recommendations/` and `/orchestration/*` request bodies is **external/untrusted** (post-API-key). Audit every kwarg it touches.
- `IntelligenceProvider.config` (DB) is **admin-trusted**. The DB-vs-payload split is enforced by `BLOCKED_CONFIG_KEYS` stripping caller kwargs while leaving DB config intact.
- Never log raw `provider_config`, prompts, completions, or API keys. Use `trace_id`-keyed structured logs only.

### Audit checks before merging
- [ ] New provider added: `validate_safe_url` called in `__init__` if it accepts a URL/host; key added to `BLOCKED_CONFIG_KEYS` if `IntelligenceProvider.config` should be the only source.
- [ ] New `__init__` kwarg added: classified per the table above; mitigation applied.
- [ ] `_redact_config` updated to cover any new sensitive key.
- [ ] No new path-walking code without `resolve_safe_path` per-entry validation if attacker reach is possible.
- [ ] Run `uv run pytest apps/intelligence/_tests/providers/test_registry.py::TestBlockedConfigKeys` — this is the Finding 1 regression suite.
