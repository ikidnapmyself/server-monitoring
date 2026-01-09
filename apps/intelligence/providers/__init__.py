"""
Intelligence providers registry.

Providers analyze system state and incidents to generate actionable recommendations.
"""

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


def get_provider(name: str, **kwargs) -> BaseProvider:
    """
    Get a provider instance by name.

    Args:
        name: Provider name (e.g., 'local').
        **kwargs: Provider-specific configuration.

    Returns:
        Configured provider instance.

    Raises:
        KeyError: If provider name is not registered.
    """
    if name not in PROVIDERS:
        raise KeyError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[name](**kwargs)


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(PROVIDERS.keys())


__all__ = [
    "BaseProvider",
    "Recommendation",
    "RecommendationPriority",
    "RecommendationType",
    "LocalRecommendationProvider",
    "get_local_recommendations",
    "get_provider",
    "list_providers",
    "PROVIDERS",
]
