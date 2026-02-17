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

    def test_provider_disk_progress_callback(self):
        """Provider should call progress_callback during disk scanning."""
        progress_messages = []

        def capture_progress(msg):
            progress_messages.append(msg)

        provider = LocalRecommendationProvider(
            large_file_threshold_mb=1000,  # High threshold to scan without finding much
            progress_callback=capture_progress,
        )
        provider._get_disk_recommendations("/tmp")

        assert any("Scanning" in msg for msg in progress_messages)
        assert any("/tmp" in msg for msg in progress_messages)

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
            recommendations = provider.analyze()

            mock_mem_rec.assert_called_once()
            assert len(recommendations) >= 1

    def test_analyze_with_analysis_type_memory(self):
        """analyze(analysis_type='memory') routes to _get_memory_recommendations."""
        provider = LocalRecommendationProvider()
        with patch.object(provider, "_get_memory_recommendations") as mock_mem:
            mock_mem.return_value = []
            provider.analyze(analysis_type="memory")
            mock_mem.assert_called_once()

    def test_analyze_with_analysis_type_disk(self):
        """analyze(analysis_type='disk') routes to _get_disk_recommendations."""
        provider = LocalRecommendationProvider()
        with patch.object(provider, "_get_disk_recommendations") as mock_disk:
            mock_disk.return_value = []
            provider.analyze(analysis_type="disk")
            mock_disk.assert_called_once()

    def test_analysis_type_takes_precedence_over_incident(self):
        """analysis_type='memory' bypasses incident detection even if incident provided."""
        provider = LocalRecommendationProvider()
        incident = MagicMock()
        incident.title = "Disk Space Alert"

        with patch.object(provider, "_get_memory_recommendations") as mock_mem:
            with patch.object(provider, "_detect_incident_type") as mock_detect:
                mock_mem.return_value = []
                provider.analyze(incident, analysis_type="memory")
                mock_mem.assert_called_once()
                mock_detect.assert_not_called()

    def test_analyze_no_incident_calls_general_recommendations(self):
        """analyze(incident=None) without analysis_type calls _general_recommendations."""
        provider = LocalRecommendationProvider()
        with patch.object(provider, "_general_recommendations") as mock_general:
            mock_general.return_value = []
            result = provider.analyze(incident=None)
            mock_general.assert_called_once()
            assert result == []

    def test_analyze_unknown_incident_type_calls_general_recommendations(self):
        """analyze(incident) with unknown type falls back to _general_recommendations."""
        provider = LocalRecommendationProvider()
        incident = MagicMock()
        incident.title = "Random Alert"
        incident.description = "Something unrelated happened"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        with patch.object(provider, "_general_recommendations") as mock_general:
            mock_general.return_value = []
            result = provider.analyze(incident=incident)
            mock_general.assert_called_once()
            assert result == []

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.disk_partitions")
    def test_general_recommendations_high_memory_and_disk(self, mock_partitions, mock_memory):
        """_general_recommendations checks memory and disk, returns recs for both."""
        mock_memory.return_value = MagicMock(percent=85)

        mock_partition = MagicMock()
        mock_partition.mountpoint = "/"
        mock_partitions.return_value = [mock_partition]

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_get_memory_recommendations") as mock_mem_rec:
            with patch.object(provider, "_get_disk_recommendations") as mock_disk_rec:
                with patch("apps.intelligence.providers.local.psutil.disk_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(percent=80)
                    mem_rec = Recommendation(
                        type=RecommendationType.MEMORY,
                        priority=RecommendationPriority.HIGH,
                        title="Memory",
                        description="Memory issue",
                    )
                    disk_rec = Recommendation(
                        type=RecommendationType.DISK,
                        priority=RecommendationPriority.MEDIUM,
                        title="Disk",
                        description="Disk issue",
                    )
                    mock_mem_rec.return_value = [mem_rec]
                    mock_disk_rec.return_value = [disk_rec]

                    result = provider._general_recommendations()

                    mock_mem_rec.assert_called_once()
                    mock_disk_rec.assert_called_once_with("/")
                    assert len(result) == 2

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.disk_partitions")
    def test_general_recommendations_low_usage_returns_empty(self, mock_partitions, mock_memory):
        """_general_recommendations with low memory/disk returns empty list."""
        mock_memory.return_value = MagicMock(percent=50)

        mock_partition = MagicMock()
        mock_partition.mountpoint = "/"
        mock_partitions.return_value = [mock_partition]

        provider = LocalRecommendationProvider()
        with patch("apps.intelligence.providers.local.psutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(percent=50)
            result = provider._general_recommendations()
            assert result == []

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.disk_partitions")
    def test_general_recommendations_disk_permission_error(self, mock_partitions, mock_memory):
        """_general_recommendations handles PermissionError from disk_usage."""
        mock_memory.return_value = MagicMock(percent=50)

        mock_partition = MagicMock()
        mock_partition.mountpoint = "/protected"
        mock_partitions.return_value = [mock_partition]

        provider = LocalRecommendationProvider()
        with patch("apps.intelligence.providers.local.psutil.disk_usage") as mock_usage:
            mock_usage.side_effect = PermissionError("access denied")
            result = provider._general_recommendations()
            assert result == []


class TestGetLocalRecommendations:
    """Tests for the get_local_recommendations convenience function."""

    @patch("apps.intelligence.providers.local.LocalRecommendationProvider.run")
    def test_get_local_recommendations_without_incident(self, mock_run):
        """get_local_recommendations() calls provider.run(incident=None)."""
        from apps.intelligence.providers.local import get_local_recommendations

        mock_run.return_value = []
        result = get_local_recommendations()
        mock_run.assert_called_once_with(incident=None)
        assert result == []

    @patch("apps.intelligence.providers.local.LocalRecommendationProvider.run")
    def test_get_local_recommendations_with_incident(self, mock_run):
        """get_local_recommendations(incident) passes incident to provider.run."""
        from apps.intelligence.providers.local import get_local_recommendations

        fake_incident = MagicMock()
        mock_run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="Test",
                description="Test",
            )
        ]
        result = get_local_recommendations(incident=fake_incident)
        mock_run.assert_called_once_with(incident=fake_incident)
        assert len(result) == 1


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
