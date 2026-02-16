# AnalysisRun Audit Logging via `BaseProvider.run()`

## Context

`/admin/intelligence/analysisrun/` shows no rows because no production code creates `AnalysisRun` records. The model exists with a full lifecycle API (`mark_started`, `mark_succeeded`, `mark_failed`) but is never wired into any execution path.

Additionally, `BaseProvider` exposes two redundant abstract methods — `analyze(incident)` and `get_recommendations()` — that both providers already collapse internally (`analyze(None)` delegates to `get_recommendations()`). Every call site duplicates the same if/else routing between them.

## Goals

1. Every provider execution creates an `AnalysisRun` audit record — pipeline, management command, and view
2. Collapse the two-method provider interface into a single `analyze(incident=None, analysis_type="")`
3. Sensitive provider config (API keys) is redacted before storage
4. Audit logging never breaks analysis execution (DB failures caught and logged)

## Design

### BaseProvider interface

Remove `get_recommendations()` as an abstract method. Add `run()` and `_redact_config()`:

```python
class BaseProvider(ABC):
    name: str = "base"
    description: str = "Base intelligence provider"

    @abstractmethod
    def analyze(
        self,
        incident: Any | None = None,
        analysis_type: str = "",
    ) -> list[Recommendation]:
        """Single public analysis method."""
        ...

    def run(
        self,
        *,
        incident: Any | None = None,
        analysis_type: str = "",
        trace_id: str = "",
        pipeline_run_id: str = "",
        provider_config: dict | None = None,
    ) -> list[Recommendation]:
        """Wrap analyze(), manage AnalysisRun lifecycle, return recommendations."""
        ...

    @staticmethod
    def _redact_config(config: dict) -> dict:
        """Mask sensitive keys before storage."""
        ...
```

### `run()` lifecycle

1. Create `AnalysisRun` in PENDING state with provider info, incident FK, trace/pipeline IDs, and redacted config
2. Call `mark_started()`
3. Call `self.analyze(incident, analysis_type)`
4. On success: `mark_succeeded(recommendations=[r.to_dict() for r in recs])`
5. On exception: `mark_failed(error_message=str(exc))`, then re-raise
6. DB failures (creating/updating AnalysisRun) caught and logged — never break analysis

Key difference from `CheckRun`: provider exceptions are **re-raised** after `mark_failed()` because callers (executor, views) have their own error handling. `CheckRun` swallows exceptions into an UNKNOWN result because checkers must always return a result.

### Config redaction

```python
SENSITIVE_PATTERNS = {"key", "secret", "token", "password", "api"}

@staticmethod
def _redact_config(config: dict) -> dict:
    return {
        k: "***" if any(p in k.lower() for p in SENSITIVE_PATTERNS) else v
        for k, v in config.items()
    }
```

### Provider changes

**LocalRecommendationProvider:**
- `analyze(incident=None, analysis_type="")` becomes the single entry point
- `analysis_type="memory"` → calls `_get_memory_recommendations()`
- `analysis_type="disk"` → calls `_get_disk_recommendations()`
- `incident` provided → existing incident-type detection (unchanged)
- Neither → general system scan (current `get_recommendations()` body inlined)
- `get_recommendations()` deleted

**OpenAIRecommendationProvider:**
- `analyze(incident=None, analysis_type="")` — returns `[]` when no incident (unchanged behavior)
- `analysis_type` ignored (OpenAI is incident-driven only)
- `get_recommendations()` deleted

### Call sites

All become `provider.run(...)`:

| File | Before | After |
|------|--------|-------|
| `apps/orchestration/executors.py` | `provider.analyze(incident)` / `provider.get_recommendations()` | `provider.run(incident=incident, trace_id=ctx.trace_id, pipeline_run_id=ctx.run_id, provider_config=provider_config)` |
| `apps/intelligence/management/commands/get_recommendations.py` | `provider.analyze(incident)` / `provider._get_memory_recommendations()` / `provider._get_disk_recommendations()` / `provider.get_recommendations()` | `provider.run(incident=incident, analysis_type=...)` |
| `apps/intelligence/views/recommendations.py` | `provider.analyze(incident)` / `provider.get_recommendations()` | `provider.run(incident=incident)` |
| `apps/intelligence/views/memory.py` | `provider._get_memory_recommendations()` | `provider.run(analysis_type="memory")` |
| `apps/intelligence/views/disk.py` | `provider._get_disk_recommendations(path)` | `provider.run(analysis_type="disk")` |
| `apps/intelligence/providers/local.py` | `get_local_recommendations()` convenience function | Update to call `provider.run()` |

### Testing

- **BaseProvider.run()**: FakeProvider with ~8 tests mirroring CheckRun pattern — lifecycle states, duration, trace_id, pipeline_run_id, incident FK, DB failure resilience, exception handling, config redaction
- **Provider refactor**: Update existing local/openai tests — `get_recommendations()` → `analyze()`, verify `analysis_type` routing in local provider
- **Call sites**: Update existing executor/command/view tests to verify `run()` is called

### Files touched

| File | Change |
|------|--------|
| `apps/intelligence/providers/base.py` | Remove `get_recommendations()` abstract, add `run()`, `_redact_config()` |
| `apps/intelligence/providers/local.py` | Merge `get_recommendations()` into `analyze()`, add `analysis_type` param |
| `apps/intelligence/providers/openai.py` | Remove `get_recommendations()`, add `analysis_type` param |
| `apps/orchestration/executors.py` | Replace `analyze()`/`get_recommendations()` routing → `provider.run()` |
| `apps/intelligence/management/commands/get_recommendations.py` | Route all modes through `provider.run()` |
| `apps/intelligence/views/recommendations.py` | `provider.run()` |
| `apps/intelligence/views/memory.py` | `provider.run(analysis_type="memory")` |
| `apps/intelligence/views/disk.py` | `provider.run(analysis_type="disk")` |
| `apps/intelligence/_tests/...` | New and updated tests |

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where to manage AnalysisRun lifecycle | `BaseProvider.run()` | Mirrors CheckRun pattern, single instrumentation point |
| No incident available | `incident=None` is fine | FK is already nullable, admin handles it |
| Provider config storage | Redacted | Mask keys matching sensitive patterns, useful for debugging |
| Scope of audit logging | All entry points | Security: every provider execution must have an audit trail |
| Two-method split | Collapse into `analyze(incident=None)` | `get_recommendations()` is redundant, both providers already delegate internally |
| Targeted analysis modes | `analysis_type` hint parameter | Routes through `run()` for audit logging while preserving diagnostic shortcuts |
| Provider exceptions | Re-raise after `mark_failed()` | Callers have their own error handling, unlike CheckRun which swallows into UNKNOWN |
