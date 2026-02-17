"""Tests for the OpenAIRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from apps.intelligence.providers import (
    RecommendationPriority,
    RecommendationType,
)
from apps.intelligence.providers.openai import OpenAIRecommendationProvider


class TestOpenAIProviderInitialization:
    """Tests for OpenAI provider initialization."""

    def test_initialization_defaults(self):
        """Test provider initializes with environment variable defaults."""
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_MODEL": "gpt-4o",
                "OPENAI_MAX_TOKENS": "2048",
            },
        ):
            provider = OpenAIRecommendationProvider()

            assert provider.api_key == "test-key"
            assert provider.model == "gpt-4o"
            assert provider.max_tokens == 2048

    def test_initialization_custom_values(self):
        """Test provider initializes with custom values."""
        provider = OpenAIRecommendationProvider(
            api_key="custom-key",
            model="gpt-4-turbo",
            max_tokens=4096,
        )

        assert provider.api_key == "custom-key"
        assert provider.model == "gpt-4-turbo"
        assert provider.max_tokens == 4096

    def test_initialization_defaults_without_env(self):
        """Test provider uses hardcoded defaults when env vars not set."""
        with patch.dict("os.environ", {}, clear=True):
            provider = OpenAIRecommendationProvider()

            assert provider.api_key is None
            assert provider.model == "gpt-4o-mini"
            assert provider.max_tokens == 1024

    def test_lazy_client_initialization(self):
        """Test that client is not initialized until accessed."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        assert provider._client is None

    @patch("openai.OpenAI")
    def test_client_property_creates_client(self, mock_openai_class):
        """Test that accessing client property creates the client."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(api_key="test-key")
        client = provider.client

        mock_openai_class.assert_called_once_with(api_key="test-key")
        assert client == mock_client

    @patch("openai.OpenAI")
    def test_client_property_reuses_client(self, mock_openai_class):
        """Test that client property returns same instance on multiple calls."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(api_key="test-key")

        # Access client multiple times
        client1 = provider.client
        client2 = provider.client

        # Should only create client once
        assert mock_openai_class.call_count == 1
        assert client1 is client2


class TestBuildPrompt:
    """Tests for prompt building."""

    def test_build_prompt_basic(self):
        """Test building prompt with basic incident data."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.title = "High Memory Usage"
        incident.description = "Memory usage exceeded 90%"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        prompt = provider._build_prompt(incident)

        assert "High Memory Usage" in prompt
        assert "Memory usage exceeded 90%" in prompt

    def test_build_prompt_with_severity_and_status(self):
        """Test building prompt with severity and status."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.title = "Disk Full"
        incident.description = "Disk usage at 95%"
        incident.severity = "critical"
        incident.status = "open"
        del incident.alerts
        del incident.metadata

        prompt = provider._build_prompt(incident)

        assert "Disk Full" in prompt
        assert "critical" in prompt
        assert "open" in prompt

    def test_build_prompt_with_alerts(self):
        """Test building prompt with associated alerts."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        alert1 = MagicMock()
        alert1.name = "Memory Alert"
        alert1.description = "High memory usage detected"

        alert2 = MagicMock()
        alert2.name = "Swap Alert"
        alert2.description = "Swap usage increasing"

        incident = MagicMock()
        incident.title = "Memory Incident"
        incident.description = "Multiple memory alerts"
        incident.alerts.all.return_value = [alert1, alert2]
        del incident.severity
        del incident.status
        del incident.metadata

        prompt = provider._build_prompt(incident)

        assert "Memory Alert" in prompt
        assert "Swap Alert" in prompt
        assert "Associated Alerts:" in prompt

    def test_build_prompt_with_metadata(self):
        """Test building prompt with metadata."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.title = "Test Incident"
        incident.description = "Test description"
        incident.metadata = {"host": "server01", "path": "/var/log"}
        del incident.severity
        del incident.status
        del incident.alerts

        prompt = provider._build_prompt(incident)

        assert "server01" in prompt
        assert "/var/log" in prompt


class TestParseResponse:
    """Tests for response parsing."""

    def test_parse_response_valid_json(self):
        """Test parsing valid JSON response."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            [
                {
                    "type": "memory",
                    "priority": "high",
                    "title": "High Memory Usage",
                    "description": "Memory is running low",
                    "actions": ["Restart service", "Add more RAM"],
                }
            ]
        )

        recommendations = provider._parse_response(response, incident_id=123)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.MEMORY
        assert recommendations[0].priority == RecommendationPriority.HIGH
        assert recommendations[0].title == "High Memory Usage"
        assert recommendations[0].incident_id == 123
        assert len(recommendations[0].actions) == 2

    def test_parse_response_with_code_blocks(self):
        """Test parsing response wrapped in markdown code blocks."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = """```json
[{"type": "disk", "priority": "critical", "title": "Disk Full", "description": "No space left", "actions": ["Clean logs"]}]
```"""

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.DISK
        assert recommendations[0].priority == RecommendationPriority.CRITICAL

    def test_parse_response_single_object(self):
        """Test parsing response with single object instead of array."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            {
                "type": "cpu",
                "priority": "medium",
                "title": "CPU Spike",
                "description": "CPU usage spike detected",
                "actions": ["Check processes"],
            }
        )

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.CPU

    def test_parse_response_invalid_json(self):
        """Test parsing invalid JSON response."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = "This is not valid JSON, but it's useful analysis."

        recommendations = provider._parse_response(response, incident_id=456)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.GENERAL
        assert recommendations[0].priority == RecommendationPriority.MEDIUM
        assert "This is not valid JSON" in recommendations[0].description

    def test_parse_response_unknown_type_and_priority(self):
        """Test parsing response with unknown type and priority."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            [
                {
                    "type": "unknown_type",
                    "priority": "unknown_priority",
                    "title": "Test",
                    "description": "Test description",
                    "actions": [],
                }
            ]
        )

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 1
        # Should default to GENERAL and MEDIUM
        assert recommendations[0].type == RecommendationType.GENERAL
        assert recommendations[0].priority == RecommendationPriority.MEDIUM

    def test_parse_response_missing_description_skipped(self):
        """Test that items without description are skipped."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            [
                {"type": "memory", "priority": "high", "title": "No Desc"},
                {
                    "type": "disk",
                    "priority": "low",
                    "title": "With Desc",
                    "description": "Has description",
                },
            ]
        )

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 1
        assert recommendations[0].title == "With Desc"


class TestAnalyzeIncident:
    """Tests for incident analysis."""

    @patch.object(OpenAIRecommendationProvider, "_call_openai")
    def test_analyze_incident_success(self, mock_call_openai):
        """Test successful incident analysis."""
        mock_call_openai.return_value = json.dumps(
            [
                {
                    "type": "memory",
                    "priority": "high",
                    "title": "Memory Issue",
                    "description": "Memory analysis result",
                    "actions": ["Action 1"],
                }
            ]
        )

        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 123
        incident.title = "Test Incident"
        incident.description = "Test description"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        recommendations = provider.analyze(incident)

        assert len(recommendations) == 1
        assert recommendations[0].incident_id == 123
        mock_call_openai.assert_called_once()

    @patch.object(OpenAIRecommendationProvider, "_call_openai")
    def test_analyze_incident_api_error(self, mock_call_openai):
        """Test graceful handling of API errors."""
        mock_call_openai.side_effect = Exception("API Error")

        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 456
        incident.title = "Test"
        incident.description = "Test"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        recommendations = provider.analyze(incident)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.GENERAL
        assert "API Error" in recommendations[0].description
        assert recommendations[0].incident_id == 456

    def test_analyze_without_incident(self):
        """Test analyze returns empty list without incident."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        recommendations = provider.analyze(None)

        assert recommendations == []


class TestCallOpenAI:
    """Tests for OpenAI API calls."""

    @patch("openai.OpenAI")
    def test_call_openai_success(self, mock_openai_class):
        """Test successful OpenAI API call."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(
            api_key="test-key",
            model="gpt-4o-mini",
            max_tokens=1024,
        )

        result = provider._call_openai("Test prompt")

        assert result == '{"test": "response"}'
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-mini"
        assert call_kwargs["max_tokens"] == 1024
        assert len(call_kwargs["messages"]) == 2


class TestProviderRegistration:
    """Tests for provider registration."""

    def test_openai_provider_in_registry(self):
        """Test that OpenAI provider is registered."""
        from apps.intelligence.providers import PROVIDERS

        assert "openai" in PROVIDERS
        assert PROVIDERS["openai"] == OpenAIRecommendationProvider

    def test_get_openai_provider(self):
        """Test getting OpenAI provider through registry."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("openai", api_key="test-key")
        assert isinstance(provider, OpenAIRecommendationProvider)
        assert provider.api_key == "test-key"

    def test_list_providers_includes_openai(self):
        """Test that list_providers includes openai."""
        from apps.intelligence.providers import list_providers

        providers = list_providers()
        assert "openai" in providers
        assert "local" in providers


class TestProviderAttributes:
    """Tests for provider class attributes."""

    def test_provider_name(self):
        """Test provider name attribute."""
        provider = OpenAIRecommendationProvider(api_key="test-key")
        assert provider.name == "openai"

    def test_provider_description(self):
        """Test provider description attribute."""
        provider = OpenAIRecommendationProvider(api_key="test-key")
        assert provider.description == "OpenAI-powered incident analysis"

    def test_system_prompt_exists(self):
        """Test that system prompt is defined."""
        assert OpenAIRecommendationProvider.SYSTEM_PROMPT
        assert "JSON" in OpenAIRecommendationProvider.SYSTEM_PROMPT
        assert "recommendation" in OpenAIRecommendationProvider.SYSTEM_PROMPT.lower()


class TestParseRecommendationItem:
    """Tests for _parse_recommendation_item method."""

    def test_parse_all_recommendation_types(self):
        """Test parsing all valid recommendation types."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        type_map = {
            "memory": RecommendationType.MEMORY,
            "disk": RecommendationType.DISK,
            "cpu": RecommendationType.CPU,
            "process": RecommendationType.PROCESS,
            "network": RecommendationType.NETWORK,
            "general": RecommendationType.GENERAL,
        }

        for type_str, expected_type in type_map.items():
            item = {
                "type": type_str,
                "priority": "medium",
                "title": f"Test {type_str}",
                "description": f"Description for {type_str}",
            }
            rec = provider._parse_recommendation_item(item)
            assert rec.type == expected_type

    def test_parse_all_priority_levels(self):
        """Test parsing all valid priority levels."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        priority_map = {
            "low": RecommendationPriority.LOW,
            "medium": RecommendationPriority.MEDIUM,
            "high": RecommendationPriority.HIGH,
            "critical": RecommendationPriority.CRITICAL,
        }

        for priority_str, expected_priority in priority_map.items():
            item = {
                "type": "general",
                "priority": priority_str,
                "title": f"Test {priority_str}",
                "description": f"Description for {priority_str}",
            }
            rec = provider._parse_recommendation_item(item)
            assert rec.priority == expected_priority

    def test_parse_actions_as_string(self):
        """Test that string actions are converted to list."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        item = {
            "type": "general",
            "priority": "low",
            "title": "Test",
            "description": "Description",
            "actions": "Single action as string",
        }

        rec = provider._parse_recommendation_item(item)
        assert rec.actions == ["Single action as string"]

    def test_parse_missing_title_uses_default(self):
        """Test that missing title uses default value."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        item = {
            "type": "general",
            "priority": "low",
            "description": "Description",
        }

        rec = provider._parse_recommendation_item(item)
        assert rec.title == "AI Recommendation"

    def test_parse_empty_description_returns_none(self):
        """Test that empty description returns None."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        item = {
            "type": "general",
            "priority": "low",
            "title": "Test",
            "description": "",
        }

        rec = provider._parse_recommendation_item(item)
        assert rec is None

    def test_parse_with_incident_id(self):
        """Test that incident_id is passed through."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        item = {
            "type": "memory",
            "priority": "high",
            "title": "Test",
            "description": "Description",
        }

        rec = provider._parse_recommendation_item(item, incident_id=999)
        assert rec.incident_id == 999

    def test_parse_case_insensitive_type(self):
        """Test that type parsing is case insensitive."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        item = {
            "type": "MEMORY",
            "priority": "HIGH",
            "title": "Test",
            "description": "Description",
        }

        rec = provider._parse_recommendation_item(item)
        assert rec.type == RecommendationType.MEMORY
        assert rec.priority == RecommendationPriority.HIGH


class TestBuildPromptEdgeCases:
    """Additional tests for prompt building edge cases."""

    def test_build_prompt_limits_alerts_to_10(self):
        """Test that prompt limits alerts to 10."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        # Create 15 mock alerts
        alerts = []
        for i in range(15):
            alert = MagicMock()
            alert.name = f"Alert {i}"
            alert.description = f"Description {i}"
            alerts.append(alert)

        incident = MagicMock()
        incident.title = "Test"
        incident.description = "Test"
        incident.alerts.all.return_value = alerts
        del incident.severity
        del incident.status
        del incident.metadata

        prompt = provider._build_prompt(incident)

        # Should only include first 10 alerts
        assert "Alert 0" in prompt
        assert "Alert 9" in prompt
        assert "Alert 10" not in prompt
        assert "Alert 14" not in prompt

    def test_build_prompt_with_all_fields(self):
        """Test building prompt with all fields present."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        alert = MagicMock()
        alert.name = "Test Alert"
        alert.description = "Alert description"

        incident = MagicMock()
        incident.title = "Full Incident"
        incident.description = "Full description"
        incident.severity = "critical"
        incident.status = "open"
        incident.alerts.all.return_value = [alert]
        incident.metadata = {"key": "value"}

        prompt = provider._build_prompt(incident)

        assert "Incident Title: Full Incident" in prompt
        assert "Incident Description: Full description" in prompt
        assert "Severity: critical" in prompt
        assert "Status: open" in prompt
        assert "Associated Alerts:" in prompt
        assert "Test Alert" in prompt
        assert "Metadata:" in prompt

    def test_build_prompt_alert_without_description(self):
        """Test building prompt with alert that has no description."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        alert = MagicMock()
        alert.name = "Alert Name Only"
        del alert.description

        incident = MagicMock()
        incident.title = "Test"
        incident.description = "Test"
        incident.alerts.all.return_value = [alert]
        del incident.severity
        del incident.status
        del incident.metadata

        prompt = provider._build_prompt(incident)

        assert "Alert Name Only" in prompt

    def test_build_prompt_empty_alerts(self):
        """Test building prompt with empty alerts list."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.title = "Test"
        incident.description = "Test"
        incident.alerts.all.return_value = []
        del incident.severity
        del incident.status
        del incident.metadata

        prompt = provider._build_prompt(incident)

        assert "Associated Alerts:" not in prompt


class TestParseResponseEdgeCases:
    """Additional tests for response parsing edge cases."""

    def test_parse_response_code_block_without_closing(self):
        """Test parsing response with code block that doesn't properly close."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = """```json
[{"type": "disk", "priority": "low", "title": "Test", "description": "Desc"}]
"""

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.DISK

    def test_parse_response_multiple_recommendations(self):
        """Test parsing response with multiple recommendations."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            [
                {
                    "type": "memory",
                    "priority": "high",
                    "title": "Mem",
                    "description": "Memory issue",
                },
                {
                    "type": "disk",
                    "priority": "medium",
                    "title": "Disk",
                    "description": "Disk issue",
                },
                {"type": "cpu", "priority": "low", "title": "CPU", "description": "CPU issue"},
            ]
        )

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 3
        assert recommendations[0].type == RecommendationType.MEMORY
        assert recommendations[1].type == RecommendationType.DISK
        assert recommendations[2].type == RecommendationType.CPU

    def test_parse_response_empty_array(self):
        """Test parsing empty JSON array."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = "[]"

        recommendations = provider._parse_response(response)

        assert len(recommendations) == 0

    def test_parse_response_empty_string(self):
        """Test parsing empty string response."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = ""

        recommendations = provider._parse_response(response, incident_id=123)

        # Should create a fallback recommendation
        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.GENERAL

    def test_parse_response_skips_invalid_items(self):
        """Test that invalid items in array are skipped."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        response = json.dumps(
            [
                {
                    "type": "memory",
                    "priority": "high",
                    "title": "Valid",
                    "description": "Valid item",
                },
                {"invalid": "item"},  # Missing required fields
                {
                    "type": "disk",
                    "priority": "low",
                    "title": "Also Valid",
                    "description": "Another",
                },
            ]
        )

        recommendations = provider._parse_response(response)

        # Should have 2 valid recommendations (middle one skipped due to no description)
        assert len(recommendations) == 2


class TestFallbackRecommendation:
    """Tests for fallback recommendation generation."""

    def test_fallback_with_incident_id(self):
        """Test fallback recommendation includes incident_id."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 789

        recs = provider._get_fallback_recommendation(incident, "Connection timeout")

        assert len(recs) == 1
        assert recs[0].incident_id == 789
        assert "Connection timeout" in recs[0].description

    def test_fallback_without_incident_id(self):
        """Test fallback recommendation when incident has no id attribute."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock(spec=[])  # No attributes

        recs = provider._get_fallback_recommendation(incident, "Error message")

        assert len(recs) == 1
        assert recs[0].incident_id is None
        assert recs[0].type == RecommendationType.GENERAL
        assert recs[0].priority == RecommendationPriority.MEDIUM

    def test_fallback_includes_suggested_actions(self):
        """Test fallback recommendation includes helpful actions."""
        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 1

        recs = provider._get_fallback_recommendation(incident, "API Error")

        assert len(recs[0].actions) >= 2
        assert any("manual" in action.lower() for action in recs[0].actions)


class TestCallOpenAIEdgeCases:
    """Additional tests for OpenAI API calls."""

    @patch("openai.OpenAI")
    def test_call_openai_empty_content(self, mock_openai_class):
        """Test handling when API returns None content."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(api_key="test-key")

        result = provider._call_openai("Test prompt")

        assert result == ""

    @patch("openai.OpenAI")
    def test_call_openai_uses_correct_temperature(self, mock_openai_class):
        """Test that API call uses low temperature for consistency."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(api_key="test-key")
        provider._call_openai("Test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.3

    @patch("openai.OpenAI")
    def test_call_openai_includes_system_prompt(self, mock_openai_class):
        """Test that API call includes system prompt."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = OpenAIRecommendationProvider(api_key="test-key")
        provider._call_openai("User prompt here")

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == OpenAIRecommendationProvider.SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "User prompt here"


class TestAnalyzeEdgeCases:
    """Additional tests for analyze method edge cases."""

    @patch.object(OpenAIRecommendationProvider, "_call_openai")
    def test_analyze_multiple_recommendations(self, mock_call_openai):
        """Test analyzing incident that returns multiple recommendations."""
        mock_call_openai.return_value = json.dumps(
            [
                {"type": "memory", "priority": "high", "title": "Mem", "description": "Memory"},
                {"type": "disk", "priority": "medium", "title": "Disk", "description": "Disk"},
            ]
        )

        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 100
        incident.title = "Complex Incident"
        incident.description = "Multiple issues"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        recommendations = provider.analyze(incident)

        assert len(recommendations) == 2
        assert all(r.incident_id == 100 for r in recommendations)

    @patch.object(OpenAIRecommendationProvider, "_call_openai")
    def test_analyze_builds_correct_prompt(self, mock_call_openai):
        """Test that analyze builds prompt correctly from incident."""
        mock_call_openai.return_value = "[]"

        provider = OpenAIRecommendationProvider(api_key="test-key")

        incident = MagicMock()
        incident.id = 1
        incident.title = "Memory Alert"
        incident.description = "High memory usage on server01"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        provider.analyze(incident)

        # Check the prompt passed to _call_openai
        call_args = mock_call_openai.call_args[0][0]
        assert "Memory Alert" in call_args
        assert "High memory usage on server01" in call_args
