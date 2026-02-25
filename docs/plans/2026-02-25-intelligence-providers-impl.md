# Database-Driven Intelligence Providers — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 6 new AI provider drivers + DB-driven provider model.

**Architecture:** DB model `IntelligenceProvider` stores provider config (like `NotificationChannel`). Active AI provider receives incident and produces recommendations. Local provider is the fallback/default when no AI provider is configured.

**Tech Stack:** Django, anthropic, google-genai, openai, ollama, mistralai

---

### Task 1: IntelligenceProvider Model + Migration + Admin

**Files:**
- Modify: `apps/intelligence/models.py`
- Create: `apps/intelligence/migrations/XXXX_add_intelligenceprovider.py` (auto-generated)
- Modify: `apps/intelligence/admin.py`
- Modify: `apps/intelligence/_tests/test_models.py` (or create)

**Step 1: Add IntelligenceProvider model**

```python
# In apps/intelligence/models.py

class IntelligenceProvider(models.Model):
    """Database-driven intelligence provider configuration.

    Stores provider type and credentials, similar to NotificationChannel.
    Only one provider can be active at a time.
    """

    PROVIDER_CHOICES = [
        ("openai", "OpenAI"),
        ("claude", "Claude (Anthropic)"),
        ("gemini", "Gemini (Google)"),
        ("copilot", "Copilot (GitHub)"),
        ("grok", "Grok (xAI)"),
        ("ollama", "Ollama (Local)"),
        ("mistral", "Mistral"),
    ]

    name = models.CharField(max_length=100, unique=True,
        help_text="Unique name for this provider (e.g., 'production-claude').")
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES, db_index=True,
        help_text="Provider driver type.")
    config = models.JSONField(default=dict, blank=True,
        help_text="Provider-specific config (api_key, model, max_tokens, etc.).")
    is_active = models.BooleanField(default=False, db_index=True,
        help_text="Whether this provider is the active one. Only one can be active.")
    description = models.TextField(blank=True, default="",
        help_text="Description of this provider configuration.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.name} ({self.provider}) [{status}]"

    def save(self, *args, **kwargs):
        # Ensure only one active provider at a time
        if self.is_active:
            IntelligenceProvider.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
```

**Step 2: Generate migration**

Run: `uv run python manage.py makemigrations intelligence`

**Step 3: Add admin**

```python
# In apps/intelligence/admin.py — add IntelligenceProviderAdmin

@admin.register(IntelligenceProvider)
class IntelligenceProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "provider", "is_active", "updated_at"]
    list_filter = ["provider", "is_active"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields
```

**Step 4: Write tests for the model**

Test: save() deactivates other providers, __str__, ordering.

**Step 5: Run tests**

Run: `uv run pytest apps/intelligence/_tests/ -v --tb=short`

**Step 6: Commit**

```bash
git add apps/intelligence/
git commit -m "feat: add IntelligenceProvider model for DB-driven config"
```

---

### Task 2: BaseAIProvider + Refactor OpenAI

**Files:**
- Create: `apps/intelligence/providers/ai_base.py`
- Modify: `apps/intelligence/providers/openai.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Step 1: Create BaseAIProvider**

Extract shared logic from OpenAI into `ai_base.py`:

```python
# apps/intelligence/providers/ai_base.py

class BaseAIProvider(BaseProvider):
    """Base class for all LLM-backed intelligence providers."""

    # Subclasses set these
    default_model: str = ""
    default_max_tokens: int = 1024

    def __init__(self, api_key: str = "", model: str = "", max_tokens: int = 0, **kwargs):
        self.api_key = api_key
        self.model = model or self.default_model
        self.max_tokens = max_tokens or self.default_max_tokens

    def analyze(self, incident=None, analysis_type="") -> list[Recommendation]:
        if incident is None:
            return []
        prompt = self._build_prompt(incident)
        try:
            response = self._call_api(prompt)
            return self._parse_response(response, incident_id=getattr(incident, "id", None))
        except Exception as e:
            return [self._get_fallback_recommendation(incident, str(e))]

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Make the API call. Subclasses implement this."""
        ...

    def _build_prompt(self, incident) -> str:
        """Build prompt with incident data."""
        # Extract from OpenAI's existing _build_prompt
        ...

    def _parse_response(self, response, incident_id=None) -> list[Recommendation]:
        """Parse JSON response into Recommendation objects."""
        # Extract from OpenAI's existing _parse_response
        ...

    def _get_fallback_recommendation(self, incident, error_message) -> Recommendation:
        """Return fallback when API fails."""
        # Extract from OpenAI's existing method
        ...

    SYSTEM_PROMPT = "..."  # Shared system prompt
```

**Step 2: Refactor OpenAI to extend BaseAIProvider**

The existing OpenAI provider should extend BaseAIProvider, implementing only `_call_api()`.

**Step 3: Update __init__.py**

Export BaseAIProvider.

**Step 4: Write tests**

Test BaseAIProvider._build_prompt, _parse_response, fallback.

**Step 5: Run tests and commit**

Run: `uv run pytest apps/intelligence/_tests/ -v --tb=short`

```bash
git commit -m "feat: add BaseAIProvider with shared prompt/parsing logic"
```

---

### Task 3: Claude Provider

**Files:**
- Create: `apps/intelligence/providers/claude.py`
- Create: `apps/intelligence/_tests/providers/test_claude.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Implementation:**

```python
# apps/intelligence/providers/claude.py

class ClaudeRecommendationProvider(BaseAIProvider):
    name = "claude"
    description = "Claude (Anthropic) intelligence provider"
    default_model = "claude-sonnet-4-20250514"

    def _call_api(self, prompt: str) -> str:
        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
```

**Tests:** Mock `anthropic.Anthropic`, test _call_api, test analyze end-to-end, test error handling.

**Register:** Add to PROVIDERS dict with conditional import.

**Commit:** `git commit -m "feat: add Claude intelligence provider"`

---

### Task 4: Gemini Provider

**Files:**
- Create: `apps/intelligence/providers/gemini.py`
- Create: `apps/intelligence/_tests/providers/test_gemini.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Implementation:**

```python
# apps/intelligence/providers/gemini.py

class GeminiRecommendationProvider(BaseAIProvider):
    name = "gemini"
    description = "Gemini (Google) intelligence provider"
    default_model = "gemini-2.0-flash"

    def _call_api(self, prompt: str) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model,
            system_instruction=self.SYSTEM_PROMPT)
        response = model.generate_content(prompt)
        return response.text
```

**Tests:** Mock google.generativeai, test _call_api, error handling.

**Commit:** `git commit -m "feat: add Gemini intelligence provider"`

---

### Task 5: Copilot + Grok Providers (OpenAI-compatible)

**Files:**
- Create: `apps/intelligence/providers/copilot.py`
- Create: `apps/intelligence/providers/grok.py`
- Create: `apps/intelligence/_tests/providers/test_copilot.py`
- Create: `apps/intelligence/_tests/providers/test_grok.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Implementation:** Both use OpenAI SDK with custom base_url.

```python
# copilot.py
class CopilotRecommendationProvider(BaseAIProvider):
    name = "copilot"
    description = "GitHub Copilot intelligence provider"
    default_model = "gpt-4o"

    def __init__(self, base_url="https://api.githubcopilot.com", **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url

    def _call_api(self, prompt: str) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model, max_tokens=self.max_tokens, temperature=0.3,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
```

```python
# grok.py — same pattern, base_url="https://api.x.ai/v1", default_model="grok-3-mini"
```

**Commit:** `git commit -m "feat: add Copilot and Grok intelligence providers"`

---

### Task 6: Ollama Provider

**Files:**
- Create: `apps/intelligence/providers/ollama.py`
- Create: `apps/intelligence/_tests/providers/test_ollama.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Implementation:**

```python
# ollama.py
class OllamaRecommendationProvider(BaseAIProvider):
    name = "ollama"
    description = "Ollama (local) intelligence provider"
    default_model = "llama3.2"

    def __init__(self, host="http://localhost:11434", **kwargs):
        super().__init__(**kwargs)
        self.host = host

    def _call_api(self, prompt: str) -> str:
        import ollama
        client = ollama.Client(host=self.host)
        response = client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response["message"]["content"]
```

**Commit:** `git commit -m "feat: add Ollama intelligence provider"`

---

### Task 7: Mistral Provider

**Files:**
- Create: `apps/intelligence/providers/mistral.py`
- Create: `apps/intelligence/_tests/providers/test_mistral.py`
- Modify: `apps/intelligence/providers/__init__.py`

**Implementation:**

```python
# mistral.py
class MistralRecommendationProvider(BaseAIProvider):
    name = "mistral"
    description = "Mistral intelligence provider"
    default_model = "mistral-small-latest"

    def _call_api(self, prompt: str) -> str:
        from mistralai import Mistral
        client = Mistral(api_key=self.api_key)
        response = client.chat.complete(
            model=self.model, max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
```

**Commit:** `git commit -m "feat: add Mistral intelligence provider"`

---

### Task 8: Provider Registry + get_active_provider + Orchestration Wiring

**Files:**
- Modify: `apps/intelligence/providers/__init__.py`
- Modify: `apps/orchestration/nodes/intelligence.py`
- Modify: `apps/intelligence/_tests/providers/test_local.py` (registry tests)

**Step 1: Update provider registry**

```python
# __init__.py
PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "local": LocalRecommendationProvider,
}

# Conditional registration for each AI provider
for name, module_path, class_name in [
    ("openai", "apps.intelligence.providers.openai", "OpenAIRecommendationProvider"),
    ("claude", "apps.intelligence.providers.claude", "ClaudeRecommendationProvider"),
    # ... etc
]:
    try:
        mod = importlib.import_module(module_path)
        PROVIDER_CLASSES[name] = getattr(mod, class_name)
    except ImportError:
        pass

def get_active_provider(**kwargs) -> BaseProvider:
    """Get active provider from DB, fall back to local."""
    from apps.intelligence.models import IntelligenceProvider as ProviderModel
    try:
        db_provider = ProviderModel.objects.filter(is_active=True).first()
        if db_provider and db_provider.provider in PROVIDER_CLASSES:
            cls = PROVIDER_CLASSES[db_provider.provider]
            return cls(**db_provider.config, **kwargs)
    except Exception:
        pass
    return LocalRecommendationProvider(**kwargs)
```

**Step 2: Tests + commit**

```bash
git commit -m "feat: wire DB-driven provider selection with local fallback"
```

---

### Task 9: Update .env.sample + Docs + agents.md

**Files:**
- Modify: `.env.sample`
- Modify: `apps/intelligence/agents.md`
- Modify: `apps/intelligence/README.md`

Add documentation for:
- How to configure providers via Django admin
- Config keys per provider
- Local provider as fallback/default behavior
- Migration from env-var-based OpenAI

**Commit:** `git commit -m "docs: update intelligence provider documentation"`

---

## Execution Order

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9

Tasks 3-7 are independent (parallelizable after Task 2). Task 8 depends on 3-7. Task 9 is last.

## Verification

After each task: `uv run pytest apps/intelligence/_tests/ --tb=short`
Final: `uv run coverage run -m pytest && uv run coverage report`
