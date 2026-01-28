"""Shared test fixtures for intelligence app."""

import pytest


@pytest.fixture
def local_provider():
    """Create a LocalRecommendationProvider instance for testing."""
    from apps.intelligence.providers import LocalRecommendationProvider

    return LocalRecommendationProvider(
        top_n_processes=5,
        large_file_threshold_mb=50.0,
        old_file_days=7,
    )
