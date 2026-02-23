"""Tests for the get_recommendations management command."""

import json
from io import StringIO
from unittest.mock import ANY, MagicMock, patch

import pytest
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.intelligence.providers import (
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


class TestListProviders(SimpleTestCase):
    """Tests for --list-providers option."""

    def test_list_providers(self):
        """Test listing available providers."""
        out = StringIO()
        call_command("get_recommendations", "--list-providers", stdout=out)

        output = out.getvalue()
        assert "Available providers:" in output
        assert "local" in output
        assert "openai" in output


class TestProviderSelection(SimpleTestCase):
    """Tests for provider selection and configuration."""

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_default_provider_is_local(self, mock_get_provider):
        """Test that default provider is local."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", stdout=out)

        mock_get_provider.assert_called_once()
        call_args = mock_get_provider.call_args
        assert call_args[0][0] == "local"

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_custom_provider(self, mock_get_provider):
        """Test selecting a custom provider."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        mock_get_provider.assert_called_once()
        call_args = mock_get_provider.call_args
        assert call_args[0][0] == "local"

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_provider_configuration_options(self, mock_get_provider):
        """Test provider configuration options are passed correctly."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command(
            "get_recommendations",
            "--provider=local",
            "--top-n=5",
            "--threshold-mb=50.0",
            "--old-days=7",
            stdout=out,
        )

        mock_get_provider.assert_called_once_with(
            "local",
            progress_callback=ANY,
            top_n_processes=5,
            large_file_threshold_mb=50.0,
            old_file_days=7,
        )

    def test_unknown_provider_error(self):
        """Test error when unknown provider is specified."""
        out = StringIO()
        err = StringIO()
        call_command("get_recommendations", "--provider=unknown", stdout=out, stderr=err)

        error_output = err.getvalue()
        assert "Unknown provider" in error_output or "unknown" in error_output.lower()


class TestIncidentAnalysis(TestCase):
    """Tests for incident analysis."""

    @pytest.mark.django_db
    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_analyze_incident_by_id(self, mock_get_provider):
        """Test analyzing a specific incident."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        # Create test incident
        incident = Incident.objects.create(
            title="Test Memory Incident",
            description="Memory usage high",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )

        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="Memory Issue",
                description="High memory usage detected",
                actions=["Restart service"],
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", f"--incident-id={incident.id}", stdout=out)

        output = out.getvalue()
        assert "Analyzing incident" in output
        assert "Test Memory Incident" in output
        mock_provider.run.assert_called_once()

        # Cleanup
        incident.delete()

    @pytest.mark.django_db
    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_incident_not_found(self, mock_get_provider):
        """Test error when incident ID doesn't exist."""
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        err = StringIO()
        call_command("get_recommendations", "--incident-id=99999", stdout=out, stderr=err)

        error_output = err.getvalue()
        assert "not found" in error_output.lower()


class TestRecommendationTypes(SimpleTestCase):
    """Tests for different recommendation type options."""

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_memory_recommendations(self, mock_get_provider):
        """Test --memory option calls run with analysis_type=memory."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="High Memory",
                description="Memory usage high",
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--memory", "--provider=local", stdout=out)

        mock_provider.run.assert_called_once_with(analysis_type="memory")

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_disk_recommendations(self, mock_get_provider):
        """Test --disk option calls run with analysis_type=disk."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.DISK,
                priority=RecommendationPriority.MEDIUM,
                title="Large Files",
                description="Found large files",
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--disk", "--provider=local", stdout=out)

        mock_provider.run.assert_called_once_with(analysis_type="disk")

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_disk_recommendations_with_path(self, mock_get_provider):
        """Test --disk with --path option calls run with analysis_type=disk."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command(
            "get_recommendations",
            "--disk",
            "--path=/var/log",
            "--provider=local",
            stdout=out,
        )

        mock_provider.run.assert_called_once_with(analysis_type="disk")

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_all_recommendations(self, mock_get_provider):
        """Test --all option calls run twice (memory + disk)."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--all", "--provider=local", stdout=out)

        assert mock_provider.run.call_count == 2
        mock_provider.run.assert_any_call(analysis_type="memory")
        mock_provider.run.assert_any_call(analysis_type="disk")

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_default_calls_run(self, mock_get_provider):
        """Test default behavior calls run."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        mock_provider.run.assert_called_once()


class TestOutputFormats(SimpleTestCase):
    """Tests for output formatting."""

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_json_output(self, mock_get_provider):
        """Test --json option outputs valid JSON."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="Test Recommendation",
                description="Test description",
                actions=["Action 1", "Action 2"],
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--json", "--provider=local", stdout=out)

        output = out.getvalue()
        data = json.loads(output)

        assert data["provider"] == "local"
        assert data["count"] == 1
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["title"] == "Test Recommendation"
        assert data["recommendations"][0]["type"] == "memory"
        assert data["recommendations"][0]["priority"] == "high"

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_no_recommendations_message(self, mock_get_provider):
        """Test message when no recommendations found."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "No recommendations at this time" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_recommendation_count_in_output(self, mock_get_provider):
        """Test recommendation count is displayed."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="Rec 1",
                description="Desc 1",
            ),
            Recommendation(
                type=RecommendationType.DISK,
                priority=RecommendationPriority.LOW,
                title="Rec 2",
                description="Desc 2",
            ),
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "2 recommendation(s)" in output


class TestPrintRecommendations(SimpleTestCase):
    """Tests for recommendation printing."""

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_print_recommendation_with_actions(self, mock_get_provider):
        """Test printing recommendation with actions."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.CPU,
                priority=RecommendationPriority.CRITICAL,
                title="CPU Alert",
                description="CPU usage critical",
                actions=["Kill runaway process", "Check cron jobs"],
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "CPU Alert" in output
        assert "CPU usage critical" in output
        assert "Suggested Actions:" in output
        assert "Kill runaway process" in output
        assert "Check cron jobs" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_print_recommendation_with_top_processes(self, mock_get_provider):
        """Test printing recommendation with process details."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="Memory Usage",
                description="High memory usage",
                details={
                    "top_processes": [
                        {"name": "python", "pid": 1234, "memory_percent": 25.5, "memory_mb": 512.0},
                        {"name": "nginx", "pid": 5678, "memory_percent": 10.0, "memory_mb": 200.0},
                    ]
                },
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "Top Memory Processes:" in output
        assert "python" in output
        assert "1234" in output
        assert "25.5%" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_print_recommendation_with_large_items(self, mock_get_provider):
        """Test printing recommendation with large file details."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.DISK,
                priority=RecommendationPriority.MEDIUM,
                title="Large Files",
                description="Found large files",
                details={
                    "large_items": [
                        {"path": "/var/log/big.log", "size_mb": 500.0, "is_directory": False},
                        {"path": "/var/cache", "size_mb": 1024.0, "is_directory": True},
                    ]
                },
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "Large Files/Directories:" in output
        assert "/var/log/big.log" in output
        assert "[FILE]" in output
        assert "[DIR]" in output
        assert "/var/cache" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_print_recommendation_with_old_files(self, mock_get_provider):
        """Test printing recommendation with old file details."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.DISK,
                priority=RecommendationPriority.LOW,
                title="Old Files",
                description="Found old files",
                details={
                    "old_files": [
                        {"path": "/var/log/old.log", "size_mb": 50.0, "days_old": 90},
                    ]
                },
            )
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "Old Files:" in output
        assert "/var/log/old.log" in output
        assert "90 days old" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_priority_formatting(self, mock_get_provider):
        """Test that different priorities are displayed."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.CRITICAL,
                title="Critical Issue",
                description="Critical",
            ),
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.HIGH,
                title="High Issue",
                description="High",
            ),
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.MEDIUM,
                title="Medium Issue",
                description="Medium",
            ),
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.LOW,
                title="Low Issue",
                description="Low",
            ),
        ]
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", "--provider=local", stdout=out)

        output = out.getvalue()
        assert "CRITICAL" in output
        assert "HIGH" in output
        assert "MEDIUM" in output
        assert "LOW" in output


class TestProgressCallback(SimpleTestCase):
    """Tests for progress callback functionality."""

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_get_recommendations_shows_progress(self, mock_get_provider):
        """Test that progress messages are shown in normal mode.

        Note: In non-TTY mode (like StringIO), only "Found:" messages
        appear in output. Regular spinner updates are suppressed to
        avoid spamming CI/CD logs.
        """
        mock_provider = MagicMock()
        mock_provider.run.return_value = []

        # Capture the progress callback when get_provider is called
        def capture_callback(*args, **kwargs):
            # Call the progress callback to simulate progress output
            # Use "Found:" prefix so it appears in non-TTY mode
            if "progress_callback" in kwargs and kwargs["progress_callback"]:
                kwargs["progress_callback"]("Found: /var/log/test.log (100 MB)")
            return mock_provider

        mock_get_provider.side_effect = capture_callback

        out = StringIO()
        call_command("get_recommendations", "--memory", stdout=out)

        output = out.getvalue()
        assert "Found:" in output
        assert "/var/log/test.log" in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_get_recommendations_no_progress_in_json_mode(self, mock_get_provider):
        """Test that progress messages are suppressed in JSON mode."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []

        # Capture the progress callback when get_provider is called
        def capture_callback(*args, **kwargs):
            # Call the progress callback to simulate progress output
            if "progress_callback" in kwargs and kwargs["progress_callback"]:
                kwargs["progress_callback"]("Analyzing memory")
            return mock_provider

        mock_get_provider.side_effect = capture_callback

        out = StringIO()
        call_command("get_recommendations", "--memory", "--json", stdout=out)

        output = out.getvalue()
        # Should be valid JSON with no progress text
        data = json.loads(output)
        assert "provider" in data
        # Should NOT contain progress messages
        assert "Analyzing memory" not in output

    @patch("apps.intelligence.management.commands.get_recommendations.get_provider")
    def test_progress_callback_is_passed_to_provider(self, mock_get_provider):
        """Test that progress_callback is passed to get_provider."""
        mock_provider = MagicMock()
        mock_provider.run.return_value = []
        mock_get_provider.return_value = mock_provider

        out = StringIO()
        call_command("get_recommendations", stdout=out)

        # Verify progress_callback was passed
        mock_get_provider.assert_called_once()
        call_kwargs = mock_get_provider.call_args[1]
        assert "progress_callback" in call_kwargs
        assert callable(call_kwargs["progress_callback"])
