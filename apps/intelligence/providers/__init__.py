"""
Intelligence providers registry.

Providers analyze system state and incidents to generate actionable recommendations.
"""

from typing import Callable

from apps.intelligence.providers.ai_base import BaseAIProvider
from apps.intelligence.providers.base import (
    BaseProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)
from apps.intelligence.providers.local import (
    LocalRecommendationProvider,
    get_local_recommendations,
)

# Registry of available providers
PROVIDERS: dict[str, type[BaseProvider]] = {
    "local": LocalRecommendationProvider,
}

# Conditionally register AI providers — each is guarded by its SDK availability
try:
    from apps.intelligence.providers.openai import OpenAIRecommendationProvider

    PROVIDERS["openai"] = OpenAIRecommendationProvider
except ImportError:
    OpenAIRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.claude import ClaudeRecommendationProvider

    PROVIDERS["claude"] = ClaudeRecommendationProvider
except ImportError:
    ClaudeRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.gemini import GeminiRecommendationProvider

    PROVIDERS["gemini"] = GeminiRecommendationProvider
except ImportError:
    GeminiRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.copilot import CopilotRecommendationProvider

    PROVIDERS["copilot"] = CopilotRecommendationProvider
except ImportError:
    CopilotRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.grok import GrokRecommendationProvider

    PROVIDERS["grok"] = GrokRecommendationProvider
except ImportError:
    GrokRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.ollama import OllamaRecommendationProvider

    PROVIDERS["ollama"] = OllamaRecommendationProvider
except ImportError:
    OllamaRecommendationProvider = None  # type: ignore[misc, assignment]

try:
    from apps.intelligence.providers.mistral import MistralRecommendationProvider

    PROVIDERS["mistral"] = MistralRecommendationProvider
except ImportError:
    MistralRecommendationProvider = None  # type: ignore[misc, assignment]


def get_provider(
    name: str = "local",
    progress_callback: Callable[[str], None] | None = None,
    **kwargs,
) -> BaseProvider:
    """
    Get a provider instance by name.

    Args:
        name: Provider name (e.g., 'local').
        progress_callback: Optional callback function for progress messages.
        **kwargs: Provider-specific configuration.

    Returns:
        Configured provider instance.

    Raises:
        KeyError: If provider name is not registered.
    """
    if name not in PROVIDERS:
        raise KeyError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    provider_class = PROVIDERS[name]
    if name == "local":
        # LocalRecommendationProvider accepts progress_callback, but the registry
        # types it as type[BaseProvider] which doesn't have this parameter
        return provider_class(progress_callback=progress_callback, **kwargs)  # type: ignore[call-arg]
    return provider_class(**kwargs)


def get_active_provider(**kwargs) -> BaseProvider:
    """Get the active provider from DB, falling back to local.

    Queries IntelligenceProvider for an active record.  If found and its
    driver class is available, returns a configured instance.  Otherwise
    falls back to the local provider.
    """
    import logging

    from django.db import OperationalError, ProgrammingError

    logger = logging.getLogger(__name__)

    try:
        from apps.intelligence.models import IntelligenceProvider as ProviderModel

        db_provider = ProviderModel.objects.filter(is_active=True).first()
        if db_provider and db_provider.provider in PROVIDERS:
            cls = PROVIDERS[db_provider.provider]
            return cls(**db_provider.config, **kwargs)
    except (OperationalError, ProgrammingError) as exc:
        logger.warning(
            "DB unavailable when resolving active intelligence provider, falling back to local: %s",
            exc,
            exc_info=True,
        )
    except Exception:
        logger.exception(
            "Unexpected error resolving active intelligence provider, falling back to local"
        )
    return LocalRecommendationProvider(**kwargs)


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(PROVIDERS.keys())


__all__ = [
    "BaseAIProvider",
    "BaseProvider",
    "ClaudeRecommendationProvider",
    "CopilotRecommendationProvider",
    "GeminiRecommendationProvider",
    "GrokRecommendationProvider",
    "LocalRecommendationProvider",
    "MistralRecommendationProvider",
    "OllamaRecommendationProvider",
    "OpenAIRecommendationProvider",
    "PROVIDERS",
    "Recommendation",
    "RecommendationPriority",
    "RecommendationType",
    "get_active_provider",
    "get_local_recommendations",
    "get_provider",
    "list_providers",
]
