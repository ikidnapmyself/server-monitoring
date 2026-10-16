"""Shared fixtures for the intelligence provider tests."""

from unittest.mock import patch

import pytest

from config.describing import Description


def _describe_from_subject(incident):
    return Description(headline=str(incident.subject), tone="warning")


@pytest.fixture(autouse=True)
def describe_incident():
    """Provider tests build MagicMock incidents, which the real describer can't query."""
    with patch(
        "apps.intelligence.providers.ai_base.describe_incident",
        side_effect=_describe_from_subject,
    ) as mocked:
        yield mocked
