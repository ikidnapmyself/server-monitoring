"""Tests for the LocalRecommendationProvider."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.intelligence.providers import (
    LocalRecommendationProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


class TestProviderRegistry:
    """Tests for the provider registry."""

    def test_list_providers(self):
        """Test listing available providers."""
        from apps.intelligence.providers import list_providers

        providers = list_providers()
        assert "local" in providers

    def test_get_provider(self):
        """Test getting a provider by name."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local")
        assert isinstance(provider, LocalRecommendationProvider)

    def test_get_provider_with_config(self):
        """Test getting a provider with custom configuration."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local", top_n_processes=5)
        assert provider.top_n_processes == 5

    def test_get_unknown_provider_raises(self):
        """Test that getting an unknown provider raises KeyError."""
        from apps.intelligence.providers import get_provider

        with pytest.raises(KeyError):
            get_provider("unknown_provider")


class TestRecommendation:
    """Tests for the Recommendation dataclass."""

    def test_recommendation_to_dict(self):
        """Test converting recommendation to dictionary."""
        rec = Recommendation(
            type=RecommendationType.MEMORY,
            priority=RecommendationPriority.HIGH,
            title="Test Recommendation",
            description="Test description",
            details={"key": "value"},
            actions=["Action 1", "Action 2"],
            incident_id=123,
        )

        result = rec.to_dict()

        assert result["type"] == "memory"
        assert result["priority"] == "high"
        assert result["title"] == "Test Recommendation"
        assert result["description"] == "Test description"
        assert result["details"] == {"key": "value"}
        assert result["actions"] == ["Action 1", "Action 2"]
        assert result["incident_id"] == 123


class TestLocalRecommendationProvider:
    """Tests for the LocalRecommendationProvider."""

    def test_initialization_defaults(self):
        """Test provider initializes with default values."""
        provider = LocalRecommendationProvider()

        assert provider.top_n_processes == 10
        assert provider.large_file_threshold_mb == 100.0
        assert provider.old_file_days == 30

    def test_initialization_custom_values(self):
        """Test provider initializes with custom values."""
        provider = LocalRecommendationProvider(
            top_n_processes=5,
            large_file_threshold_mb=50.0,
            old_file_days=7,
        )

        assert provider.top_n_processes == 5
        assert provider.large_file_threshold_mb == 50.0
        assert provider.old_file_days == 7

    def test_provider_calls_progress_callback(self):
        """Provider should call progress_callback during operations."""
        progress_messages = []

        def capture_progress(msg):
            progress_messages.append(msg)

        provider = LocalRecommendationProvider(
            top_n_processes=3,
            progress_callback=capture_progress,
        )
        provider._get_memory_recommendations()

        assert len(progress_messages) > 0
        assert any("memory" in msg.lower() for msg in progress_messages)

    @patch("apps.intelligence.providers.local.psutil")
    def test_get_top_memory_processes(self, mock_psutil):
        """Test getting top memory-consuming processes."""
        mock_proc1 = MagicMock()
        mock_proc1.info = {
            "pid": 1234,
            "name": "python",
            "memory_percent": 15.5,
            "memory_info": MagicMock(rss=1024 * 1024 * 100),
            "cmdline": ["python", "test.py"],
        }

        mock_proc2 = MagicMock()
        mock_proc2.info = {
            "pid": 5678,
            "name": "nginx",
            "memory_percent": 5.0,
            "memory_info": MagicMock(rss=1024 * 1024 * 50),
            "cmdline": ["nginx"],
        }

        mock_psutil.process_iter.return_value = [mock_proc1, mock_proc2]

        provider = LocalRecommendationProvider()
        processes = provider._get_top_memory_processes()

        assert len(processes) > 0
        if len(processes) >= 2:
            assert processes[0].memory_percent >= processes[1].memory_percent

    def test_detect_incident_type_memory(self):
        """Test detecting memory incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "High Memory Usage Alert"
        incident.description = "Memory usage exceeded 90%"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "memory"

    def test_detect_incident_type_disk(self):
        """Test detecting disk incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "Disk Space Low"
        incident.description = "Storage running out on /var"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "disk"

    def test_detect_incident_type_cpu(self):
        """Test detecting CPU incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "High CPU Load"
        incident.description = "CPU usage at 95%"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "cpu"

    def test_detect_incident_type_unknown(self):
        """Test detecting unknown incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "General Alert"
        incident.description = "Something happened"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "unknown"

    def test_classify_file_log(self):
        """Test classifying log files."""
        provider = LocalRecommendationProvider()

        assert provider._classify_file(Path("/var/log/syslog.log")) == "log"
        assert provider._classify_file(Path("/var/log/app.log.1")) == "log"
        assert provider._classify_file(Path("/var/log/old.log.gz")) == "log"

    def test_classify_file_cache(self):
        """Test classifying cache files."""
        provider = LocalRecommendationProvider()

        assert provider._classify_file(Path("~/.cache/something")) == "cache"
        assert provider._classify_file(Path("/tmp/cache_file")) == "cache"

    def test_classify_file_temp(self):
        """Test classifying temp files."""
        provider = LocalRecommendationProvider()

        assert provider._classify_file(Path("/tmp/something.tmp")) == "cache"
        assert provider._classify_file(Path("/tmp/tmpfile")) == "cache"

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.disk_partitions")
    def test_get_recommendations_low_memory(self, mock_partitions, mock_memory):
        """Test get_recommendations when memory is high."""
        mock_memory.return_value = MagicMock(percent=85)
        mock_partitions.return_value = []

        provider = LocalRecommendationProvider()

        with patch.object(provider, "_get_memory_recommendations") as mock_mem_rec:
            mock_mem_rec.return_value = [
                Recommendation(
                    type=RecommendationType.MEMORY,
                    priority=RecommendationPriority.HIGH,
                    title="Test",
                    description="Test",
                )
            ]
            recommendations = provider.get_recommendations()

            mock_mem_rec.assert_called_once()
            assert len(recommendations) >= 1


@pytest.mark.django_db
class TestIntegration:
    """Integration tests requiring database access."""

    def test_analyze_with_incident(self):
        """Test analyzing a real incident."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        incident = Incident.objects.create(
            title="Memory Alert: High RAM Usage",
            description="Memory usage has exceeded 85% threshold",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )

        provider = LocalRecommendationProvider()
        recommendations = provider.analyze(incident)

        assert isinstance(recommendations, list)
        incident.delete()
