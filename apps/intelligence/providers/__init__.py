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

# Conditionally register OpenAI provider if the package is available
try:
    from apps.intelligence.providers.openai import OpenAIRecommendationProvider

    PROVIDERS["openai"] = OpenAIRecommendationProvider
except ImportError:
    OpenAIRecommendationProvider = None  # type: ignore[misc, assignment]


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


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(PROVIDERS.keys())


__all__ = [
    "BaseAIProvider",
    "BaseProvider",
    "Recommendation",
    "RecommendationPriority",
    "RecommendationType",
    "LocalRecommendationProvider",
    "OpenAIRecommendationProvider",
    "get_local_recommendations",
    "get_provider",
    "list_providers",
    "PROVIDERS",
]
