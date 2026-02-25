# Database-Driven Intelligence Providers — Design

## Goal

Replace env-var-based provider configuration with a database-driven `IntelligenceProvider` model (same pattern as `NotificationChannel`). Add 6 new AI provider drivers (Claude, Gemini, Copilot, Grok, Ollama, Mistral) alongside the existing OpenAI driver. The local provider remains the fallback/default when no AI provider is configured.

## Architecture

```
Pipeline triggers intelligence stage
        ↓
DB has active AI provider?
    YES → AI provider (from DB) → incident → LLM API → Recommendations
    NO  → Local provider (fallback) → rule-based analysis → Recommendations
        ↓
Recommendations stored in AnalysisRun
```

### Key Decisions

1. **DB-driven config** — `IntelligenceProvider` model stores provider type, credentials (JSONField), active flag. No env vars required (but OpenAI backward-compat preserved).
2. **Single active provider** — One provider is active at a time. `is_active` flag with DB constraint.
3. **Local as fallback** — Local provider runs when no AI provider is active in the DB. It provides rule-based recommendations without requiring any external API.
4. **Official SDKs** — Each provider uses its official Python SDK with lazy imports (conditional on package availability).
5. **Same prompt strategy** — All AI providers use the same system prompt and incident formatting as OpenAI. Only the API call differs.

## Model: IntelligenceProvider

```python
class IntelligenceProvider(models.Model):
    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=50, db_index=True)  # claude, gemini, etc.
    config = models.JSONField(default=dict, blank=True)  # {api_key, model, max_tokens, ...}
    is_active = models.BooleanField(default=False, db_index=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Config JSONField per provider:

| Provider | Required Config Keys | Optional Keys |
|----------|---------------------|---------------|
| openai | api_key | model (gpt-4o-mini), max_tokens (1024) |
| claude | api_key | model (claude-sonnet-4-20250514), max_tokens (1024) |
| gemini | api_key | model (gemini-2.0-flash), max_tokens (1024) |
| copilot | api_key | model (gpt-4o), max_tokens (1024), base_url |
| grok | api_key | model (grok-3-mini), max_tokens (1024), base_url (https://api.x.ai/v1) |
| ollama | — | host (http://localhost:11434), model (llama3.2), max_tokens (1024) |
| mistral | api_key | model (mistral-small-latest), max_tokens (1024) |

## Provider Drivers

### Base Changes

Refactor existing `BaseProvider` to support:
- Receiving config from DB (via `provider_config` dict)

### New Abstract: BaseAIProvider(BaseProvider)

Intermediate class for all LLM-backed providers:
- `_build_prompt(incident)` — shared prompt construction
- `_parse_response(response)` — shared JSON response parsing (already in OpenAI, extract to base)
- `_get_fallback_recommendation(incident, error_message)` — shared fallback

### Provider Implementations

| File | Class | SDK | Notes |
|------|-------|-----|-------|
| `openai.py` | OpenAIRecommendationProvider | `openai` | Refactor to use BaseAIProvider |
| `claude.py` | ClaudeRecommendationProvider | `anthropic` | Messages API |
| `gemini.py` | GeminiRecommendationProvider | `google-genai` | GenerativeModel API |
| `copilot.py` | CopilotRecommendationProvider | `openai` | OpenAI-compatible endpoint |
| `grok.py` | GrokRecommendationProvider | `openai` | OpenAI-compatible endpoint (x.ai) |
| `ollama.py` | OllamaRecommendationProvider | `ollama` | Local model, no API key needed |
| `mistral.py` | MistralRecommendationProvider | `mistralai` | Mistral chat API |

## Registration & Selection

```python
# providers/__init__.py
PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "local": LocalRecommendationProvider,
    "openai": OpenAIRecommendationProvider,
    # ... conditionally registered based on SDK availability
}

def get_active_provider(**kwargs) -> BaseProvider:
    """Get the active AI provider from DB, fall back to local."""
    from apps.intelligence.models import IntelligenceProvider as ProviderModel
    try:
        db_provider = ProviderModel.objects.filter(is_active=True).first()
        if db_provider and db_provider.provider in PROVIDER_CLASSES:
            cls = PROVIDER_CLASSES[db_provider.provider]
            return cls(**db_provider.config)
    except Exception:
        pass
    return LocalRecommendationProvider()
```

## Admin

Extend intelligence admin with:
- `IntelligenceProviderAdmin` — list display, config (redacted in display), active toggle
- Validate only one provider is active (or none = local-only mode)

## Migration Path

- Existing `OPENAI_*` env vars still work as defaults when no DB record exists
- `get_provider()` checks DB first, falls back to env-var-based OpenAI, then local
- No breaking changes to orchestration layer — it still calls `get_provider()` / `provider.run()`
