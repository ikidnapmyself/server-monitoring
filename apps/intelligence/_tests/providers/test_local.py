"""Tests for the LocalRecommendationProvider."""

import itertools
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import psutil
import pytest
from django.test import SimpleTestCase, TestCase

from apps.intelligence.providers import (
    LocalRecommendationProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)
from apps.intelligence.providers.local import (
    DU_TIMEOUT_SECONDS,
    SCAN_MAX_DEPTH,
    SCAN_TIME_BUDGET_SECONDS,
    ScanBudget,
    build_du_command,
    iter_bounded,
)

# Every mocked entry shares one st_dev with its mocked root: the scan stays on
# one filesystem (du -x), so entries on a different device are skipped, and a
# mock that does not model st_dev would be skipped for the wrong reason.
MOCK_DEV = 4242


def _mock_dir(entries=None, *, exists=True, iterdir_error=None, st_dev=MOCK_DEV):
    """A mocked directory handle that iter_bounded can walk."""
    handle = MagicMock(spec=Path)
    handle.exists.return_value = exists
    handle.is_file.return_value = False
    handle.is_dir.return_value = True
    handle.is_symlink.return_value = False
    handle.stat.return_value = MagicMock(st_dev=st_dev)
    if iterdir_error is not None:
        handle.iterdir.side_effect = iterdir_error
    else:
        handle.iterdir.return_value = list(entries or [])
    return handle


def _mock_file(
    path="/mock/file",
    *,
    size=0,
    mtime=None,
    st_dev=MOCK_DEV,
    is_file=True,
    is_file_error=None,
):
    """A mocked file entry with real booleans for is_dir()/is_symlink()."""
    entry = MagicMock(spec=Path)
    if is_file_error is not None:
        entry.is_file.side_effect = is_file_error
    else:
        entry.is_file.return_value = is_file
    entry.is_dir.return_value = not is_file
    entry.is_symlink.return_value = False
    entry.stat.return_value = MagicMock(
        st_size=size,
        st_mtime=datetime.now().timestamp() if mtime is None else mtime,
        st_dev=st_dev,
    )
    entry.name = Path(path).name
    entry.suffix = Path(path).suffix
    entry.__str__ = lambda self, value=path: value
    return entry


class TestProviderRegistry(SimpleTestCase):
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

    def test_get_provider_strips_blocked_host_kwarg(self):
        """URL-controlling kwargs are stripped to prevent SSRF."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local", host="http://evil.com")
        assert isinstance(provider, LocalRecommendationProvider)

    def test_get_provider_strips_blocked_base_url_kwarg(self):
        """base_url kwarg is stripped to prevent SSRF."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local", base_url="http://evil.com")
        assert isinstance(provider, LocalRecommendationProvider)

    def test_get_provider_passes_safe_kwargs(self):
        """Non-blocked kwargs like top_n_processes still pass through."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local", top_n_processes=3)
        assert provider.top_n_processes == 3

    def test_get_provider_strips_host_from_url_accepting_provider(self):
        """Strong proof: a provider that *does* accept `host` never sees the
        malicious value, because get_provider strips it before construction.
        """
        from unittest.mock import MagicMock, patch

        from apps.intelligence.providers import PROVIDERS, get_provider

        dummy_cls = MagicMock()
        with patch.dict(PROVIDERS, {"dummy_url_provider": dummy_cls}):
            get_provider(
                "dummy_url_provider",
                host="http://169.254.169.254",
                base_url="http://evil.com",
                model="safe-model",
            )

        # Dummy provider was constructed — but without host/base_url kwargs
        dummy_cls.assert_called_once()
        call_kwargs = dummy_cls.call_args.kwargs
        assert "host" not in call_kwargs
        assert "base_url" not in call_kwargs
        assert call_kwargs.get("model") == "safe-model"

    def test_get_active_provider_strips_blocked_kwargs(self):
        """get_active_provider also strips URL-controlling kwargs."""
        from unittest.mock import MagicMock, patch

        from apps.intelligence.providers import PROVIDERS, get_active_provider

        dummy_cls = MagicMock()
        mock_db_provider = MagicMock()
        mock_db_provider.provider = "dummy_url_provider"
        mock_db_provider.config = {"base_url": "https://server-configured.example.com"}

        with patch.dict(PROVIDERS, {"dummy_url_provider": dummy_cls}):
            with patch("apps.intelligence.models.IntelligenceProvider.objects") as mock_objects:
                mock_objects.filter.return_value.first.return_value = mock_db_provider
                get_active_provider(
                    host="http://169.254.169.254",
                    base_url="http://evil.com",
                    model="safe-model",
                )

        call_kwargs = dummy_cls.call_args.kwargs
        # Server-side DB config retains its URL
        assert call_kwargs.get("base_url") == "https://server-configured.example.com"
        # Caller-supplied host is stripped
        assert "host" not in call_kwargs
        # Safe kwargs pass through
        assert call_kwargs.get("model") == "safe-model"


class TestRecommendation(SimpleTestCase):
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


class TestLocalRecommendationProvider(SimpleTestCase):
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
        import tempfile

        progress_messages = []

        def capture_progress(msg):
            progress_messages.append(msg)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small file so the scan has something to traverse
            Path(tmpdir, "testfile.txt").write_text("hello")

            provider = LocalRecommendationProvider(
                large_file_threshold_mb=1000,  # High threshold to scan without finding much
                progress_callback=capture_progress,
                scan_paths=[tmpdir],  # Constrain _find_old_logs to tmpdir too
            )
            provider._get_disk_recommendations(tmpdir)

        assert any("Scanning" in msg for msg in progress_messages)
        assert any(tmpdir in msg for msg in progress_messages)

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


class TestGetTopCpuProcesses(SimpleTestCase):
    """Tests for _get_top_cpu_processes."""

    @patch("time.sleep")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_normal_iteration_returns_sorted(self, mock_iter, mock_sleep):
        """Normal iteration returns processes sorted by cpu_percent desc."""
        proc1 = MagicMock()
        proc1.info = {
            "pid": 100,
            "name": "heavy",
            "cpu_percent": 50.0,
            "cmdline": ["heavy", "--run"],
        }
        proc2 = MagicMock()
        proc2.info = {
            "pid": 200,
            "name": "light",
            "cpu_percent": 10.0,
            "cmdline": ["light"],
        }
        proc3 = MagicMock()
        proc3.info = {
            "pid": 300,
            "name": "idle",
            "cpu_percent": 0.5,
            "cmdline": ["idle"],
        }
        # First call is for init, second is the real iteration
        mock_iter.side_effect = [[proc1], [proc1, proc2, proc3]]

        provider = LocalRecommendationProvider()
        result = provider._get_top_cpu_processes()

        assert len(result) == 2  # proc3 filtered out (cpu < 1.0)
        assert result[0]["pid"] == 100
        assert result[0]["cpu_percent"] == 50.0
        assert result[1]["pid"] == 200

    @patch("time.sleep")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_no_such_process_exception(self, mock_iter, mock_sleep):
        """NoSuchProcess exception is caught and process is skipped."""
        good_proc = MagicMock()
        good_proc.info = {
            "pid": 10,
            "name": "ok",
            "cpu_percent": 5.0,
            "cmdline": [],
        }
        mock_iter.side_effect = [[], [good_proc]]

        provider = LocalRecommendationProvider()
        result = provider._get_top_cpu_processes()
        assert len(result) == 1

    @patch("time.sleep")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_access_denied_exception(self, mock_iter, mock_sleep):
        """AccessDenied exception is caught and process is skipped."""
        bad_proc = MagicMock()
        bad_proc.info = MagicMock()
        bad_proc.info.get = MagicMock(side_effect=psutil.AccessDenied(pid=1))
        mock_iter.side_effect = [[], [bad_proc]]

        provider = LocalRecommendationProvider()
        result = provider._get_top_cpu_processes()
        assert result == []

    @patch("time.sleep")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_zombie_process_exception(self, mock_iter, mock_sleep):
        """ZombieProcess exception is caught and process is skipped."""
        bad_proc = MagicMock()
        bad_proc.info = MagicMock()
        bad_proc.info.get = MagicMock(side_effect=psutil.ZombieProcess(pid=1))
        mock_iter.side_effect = [[], [bad_proc]]

        provider = LocalRecommendationProvider()
        result = provider._get_top_cpu_processes()
        assert result == []

    @patch("time.sleep")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_process_with_none_name(self, mock_iter, mock_sleep):
        """Process with None name gets 'unknown' as name."""
        proc = MagicMock()
        proc.info = {
            "pid": 99,
            "name": None,
            "cpu_percent": 20.0,
            "cmdline": None,
        }
        mock_iter.side_effect = [[], [proc]]

        provider = LocalRecommendationProvider()
        result = provider._get_top_cpu_processes()
        assert len(result) == 1
        assert result[0]["name"] == "unknown"
        assert result[0]["cmdline"] == ""


class TestScanLargeFiles(SimpleTestCase):
    """Tests for _scan_large_files."""

    def setUp(self):
        """Pin the resolved du path so tests do not depend on the host PATH."""
        patcher = patch(
            "apps.intelligence.providers.local.shutil.which",
            return_value="/usr/bin/du",
        )
        self.mock_which = patcher.start()
        self.addCleanup(patcher.stop)

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_scan_root_path_skips_resolve(self, mock_run, mock_stat, mock_isdir):
        """Passing '/' skips resolve_safe_path (the if root_path != '/' branch)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="204800\t/bigfile.log\n",
        )
        mock_stat.return_value = MagicMock(
            st_mtime=(datetime.now() - timedelta(days=5)).timestamp()
        )
        mock_isdir.return_value = False

        provider = LocalRecommendationProvider()
        result = provider._scan_large_files("/")

        assert len(result) == 1

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_command_success(self, mock_run, mock_stat, mock_isdir):
        """du command succeeds and returns large files."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="204800\t/var/log/bigfile.log\n102400\t/var/data\n",
        )
        mock_stat.return_value = MagicMock(
            st_mtime=(datetime.now() - timedelta(days=5)).timestamp()
        )
        mock_isdir.side_effect = [False, True]

        provider = LocalRecommendationProvider()
        result = provider._scan_large_files("/var")

        assert len(result) == 2
        assert result[0].size_mb >= result[1].size_mb  # sorted desc
        # du is asked for kilobytes (-k), so the parser's /1024 is megabytes.
        assert result[0].size_mb == 200.0
        assert result[1].size_mb == 100.0

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_empty_lines_skipped(self, mock_run, mock_stat, mock_isdir):
        """Empty lines in du output are skipped."""
        # Empty line in the middle so strip() doesn't remove it
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="204800\t/var/log/a.log\n\n102400\t/var/log/b.log",
        )
        mock_stat.return_value = MagicMock(st_mtime=datetime.now().timestamp())
        mock_isdir.return_value = False

        provider = LocalRecommendationProvider()
        result = provider._scan_large_files("/var")

        assert len(result) == 2

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_line_without_tab_skipped(self, mock_run, mock_stat, mock_isdir):
        """Lines without tab separator are skipped (len(parts) != 2)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="no-tab-here\n204800\t/var/log/good.log",
        )
        mock_stat.return_value = MagicMock(st_mtime=datetime.now().timestamp())
        mock_isdir.return_value = False

        provider = LocalRecommendationProvider()
        result = provider._scan_large_files("/var")

        assert len(result) == 1
        assert result[0].path == "/var/log/good.log"

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_output_that_parses_to_nothing_falls_back(self, mock_run):
        """du exiting 0 with output we cannot parse is not an all-clear."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="notanumber\t/var/log/badline\n",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "big.bin").write_bytes(b"x" * 4096)

            provider = LocalRecommendationProvider(large_file_threshold_mb=0.001)
            result = provider._scan_large_files(root.as_posix())

        assert [Path(item.path).name for item in result] == ["big.bin"]

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_reporting_nothing_is_a_real_all_clear(self, mock_run):
        """Empty du output means nothing over threshold -- do not re-walk."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        with patch(
            "apps.intelligence.providers.local.iter_bounded",
        ) as mock_iter:
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var")

        assert result == []
        mock_iter.assert_not_called()

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_fails_fallback_to_python(self, mock_run):
        """When du fails, Python fallback is used."""
        mock_run.side_effect = FileNotFoundError("du not found")

        mock_file = _mock_file("/fallback/bigfile", size=200 * 1024 * 1024)
        # A directory entry, which the large-file scan skips.
        mock_dir = _mock_file("/fallback/subdir", is_file=False)
        mock_scan_path = _mock_dir([mock_dir, mock_file])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/test")

        assert len(result) == 1
        assert result[0].path == "/fallback/bigfile"

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_fallback_permission_error_on_item(self, mock_run):
        """PermissionError on individual file in fallback is caught."""
        mock_run.side_effect = FileNotFoundError("du not found")

        mock_item = _mock_file("/var/test/denied", is_file_error=PermissionError("denied"))
        mock_scan_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/test")

        assert result == []

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_fallback_path_does_not_exist(self, mock_run):
        """When fallback path doesn't exist, empty list returned."""
        mock_run.side_effect = FileNotFoundError("du not found")

        mock_scan_path = _mock_dir(exists=False)

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/nonexistent")

        assert result == []

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_stat_oserror_sets_days_minus_one(self, mock_run, mock_stat, mock_isdir):
        """OSError from os.stat sets days_ago to -1."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="204800\t/var/log/nostat.log\n",
        )
        mock_stat.side_effect = OSError("stat failed")
        mock_isdir.return_value = False

        provider = LocalRecommendationProvider()
        result = provider._scan_large_files("/var")

        assert len(result) == 1
        assert result[0].modified_days_ago == -1

    @patch("apps.intelligence.providers.local.os.path.isdir")
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_old_file_reports_progress(self, mock_run, mock_stat, mock_isdir):
        """Old files (> old_file_days) trigger progress with days info."""
        progress_messages = []
        old_time = (datetime.now() - timedelta(days=60)).timestamp()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="204800\t/var/log/old.log\n",
        )
        mock_stat.return_value = MagicMock(st_mtime=old_time)
        mock_isdir.return_value = False

        provider = LocalRecommendationProvider(
            progress_callback=lambda m: progress_messages.append(m),
        )
        result = provider._scan_large_files("/var")

        assert len(result) == 1
        assert any("days old" in m for m in progress_messages)

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_nonzero_returncode_uses_fallback(self, mock_run):
        """du with nonzero returncode falls through to Python fallback."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        mock_scan_path = _mock_dir(exists=False)

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/test")

        assert result == []

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_fallback_old_file_reports_progress(self, mock_run):
        """Old files in fallback path trigger progress with days info."""
        mock_run.side_effect = FileNotFoundError("du not found")
        progress_messages = []

        old_time = (datetime.now() - timedelta(days=60)).timestamp()
        mock_item = _mock_file("/fallback/old.log", size=200 * 1024 * 1024, mtime=old_time)
        mock_scan_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider(
                progress_callback=lambda m: progress_messages.append(m),
            )
            result = provider._scan_large_files("/var/test")

        assert len(result) == 1
        assert any("days old" in m for m in progress_messages)

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_is_invoked_with_the_du_timeout(self, mock_run):
        """The subprocess timeout pins DU_TIMEOUT_SECONDS, not the scan budget."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        LocalRecommendationProvider()._scan_large_files("/var")

        assert mock_run.call_args.kwargs["timeout"] == DU_TIMEOUT_SECONDS

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_subprocess_timeout_falls_through(self, mock_run):
        """subprocess.TimeoutExpired falls through to Python fallback."""
        mock_run.side_effect = TimeoutExpired(cmd=["du"], timeout=DU_TIMEOUT_SECONDS)  # nosemgrep

        mock_scan_path = _mock_dir(exists=False)

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/test")

        assert result == []

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_fallback_progress_every_100_files(self, mock_run):
        """Fallback reports progress every 100 files."""
        mock_run.side_effect = FileNotFoundError("du not found")
        progress_messages = []

        # 101 mock files, all under threshold so none are "large".
        mock_items = [_mock_file(f"/file{index}", size=100) for index in range(101)]
        mock_scan_path = _mock_dir(mock_items)

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_scan_path,
        ):
            provider = LocalRecommendationProvider(
                progress_callback=lambda m: progress_messages.append(m),
            )
            result = provider._scan_large_files("/var/test")

        assert any("Scanning... 100 files" in m for m in progress_messages)
        assert result == []

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_fallback_outer_exception_caught(self, mock_run):
        """Outer exception in fallback (e.g. expanduser fails) is caught."""
        mock_run.side_effect = FileNotFoundError("du not found")

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            side_effect=RuntimeError("unexpected"),
        ):
            provider = LocalRecommendationProvider()
            result = provider._scan_large_files("/var/test")

        assert result == []


class TestFindOldLogs(SimpleTestCase):
    """Tests for _find_old_logs."""

    def test_normal_operation_finds_old_files(self):
        """Finds old files larger than 1MB."""
        old_time = (datetime.now() - timedelta(days=60)).timestamp()
        mock_item = _mock_file("/var/log/old.log", size=5 * 1024 * 1024, mtime=old_time)
        mock_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/var/log"],
            )
            result = provider._find_old_logs()

        assert len(result) == 1
        assert result[0].days_old >= 59

    def test_non_files_are_skipped(self):
        """Directories and other non-files are skipped."""
        mock_item = _mock_file("/var/log/subdir", is_file=False)
        mock_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/var/log"],
            )
            result = provider._find_old_logs()

        assert result == []

    def test_small_files_under_1mb_skipped(self):
        """Files under 1MB are skipped to reduce noise."""
        old_time = (datetime.now() - timedelta(days=60)).timestamp()
        mock_item = _mock_file("/var/log/small.log", size=500 * 1024, mtime=old_time)
        mock_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/var/log"],
            )
            result = provider._find_old_logs()

        assert result == []

    def test_permission_error_on_item_skipped(self):
        """PermissionError on individual item is caught and skipped."""
        mock_item = _mock_file("/var/log/denied", is_file_error=PermissionError("denied"))
        mock_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/var/log"],
            )
            result = provider._find_old_logs()

        assert result == []

    def test_permission_error_on_directory_skipped(self):
        """PermissionError on scan directory is caught and skipped."""
        mock_path = _mock_dir(iterdir_error=PermissionError("denied on dir"))

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/protected"],
            )
            result = provider._find_old_logs()

        assert result == []

    def test_path_does_not_exist_skipped(self):
        """Non-existent scan path is skipped."""
        mock_path = _mock_dir(exists=False)

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/nonexistent"],
            )
            result = provider._find_old_logs()

        assert result == []

    def test_recent_files_are_skipped(self):
        """Files modified recently (within old_file_days) are skipped."""
        recent_time = (datetime.now() - timedelta(days=5)).timestamp()
        mock_item = _mock_file("/var/log/recent.log", size=5 * 1024 * 1024, mtime=recent_time)
        mock_path = _mock_dir([mock_item])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(
                scan_paths=["/var/log"],
                old_file_days=30,
            )
            result = provider._find_old_logs()

        assert result == []


class TestClassifyFile(SimpleTestCase):
    """Tests for _classify_file — specifically the 'other' branch."""

    def test_other_category(self):
        """File with unrecognized extension returns 'other'."""
        provider = LocalRecommendationProvider()
        # Path not in cache/tmp patterns, not a log or temp ext
        result = provider._classify_file(Path("/home/user/data.csv"))
        assert result == "other"

    def test_other_for_unknown_suffix(self):
        """File with .xyz suffix in non-special directory returns 'other'."""
        provider = LocalRecommendationProvider()
        result = provider._classify_file(Path("/home/user/file.xyz"))
        assert result == "other"

    def test_temp_category_by_suffix(self):
        """File with .temp suffix in non-cache path returns 'temp'."""
        provider = LocalRecommendationProvider()
        # Override CACHE_PATTERNS so cache check doesn't match first
        provider.CACHE_PATTERNS = {"cache", ".cache"}
        result = provider._classify_file(Path("/home/user/file.temp"))
        assert result == "temp"

    def test_temp_category_by_name_prefix(self):
        """File with name starting with 'tmp' returns 'temp'."""
        provider = LocalRecommendationProvider()
        # Override CACHE_PATTERNS and LOG_EXTENSIONS to isolate temp check
        provider.CACHE_PATTERNS = {"cache", ".cache"}
        provider.LOG_EXTENSIONS = {".log", ".log.gz"}
        result = provider._classify_file(Path("/home/user/tmpdata.dat"))
        assert result == "temp"


class TestGetCpuRecommendations(SimpleTestCase):
    """Tests for _get_cpu_recommendations."""

    @patch("apps.intelligence.providers.local.psutil.cpu_percent")
    @patch("apps.intelligence.providers.local.psutil.cpu_count")
    @patch("apps.intelligence.providers.local.psutil.getloadavg")
    def test_with_processes_critical(self, mock_loadavg, mock_cpu_count, mock_cpu_pct):
        """CPU > 90% returns CRITICAL priority recommendation."""
        mock_cpu_pct.return_value = 95.0
        mock_cpu_count.return_value = 4
        mock_loadavg.return_value = (3.5, 3.0, 2.5)

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_top_cpu_processes",
            return_value=[
                {
                    "pid": 1,
                    "name": "heavy",
                    "cpu_percent": 80.0,
                    "cmdline": "heavy --run",
                },
            ],
        ):
            result = provider._get_cpu_recommendations(incident_id=42)

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.CRITICAL
        assert result[0].incident_id == 42

    @patch("apps.intelligence.providers.local.psutil.cpu_percent")
    @patch("apps.intelligence.providers.local.psutil.cpu_count")
    @patch("apps.intelligence.providers.local.psutil.getloadavg")
    def test_with_processes_high(self, mock_loadavg, mock_cpu_count, mock_cpu_pct):
        """CPU > 80% returns HIGH priority recommendation."""
        mock_cpu_pct.return_value = 85.0
        mock_cpu_count.return_value = 4
        mock_loadavg.return_value = (2.5, 2.0, 1.5)

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_top_cpu_processes",
            return_value=[
                {
                    "pid": 1,
                    "name": "busy",
                    "cpu_percent": 60.0,
                    "cmdline": "",
                },
            ],
        ):
            result = provider._get_cpu_recommendations()

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.HIGH

    @patch("apps.intelligence.providers.local.psutil.cpu_percent")
    @patch("apps.intelligence.providers.local.psutil.cpu_count")
    @patch("apps.intelligence.providers.local.psutil.getloadavg")
    def test_with_processes_medium(self, mock_loadavg, mock_cpu_count, mock_cpu_pct):
        """CPU <= 80% returns MEDIUM priority recommendation."""
        mock_cpu_pct.return_value = 70.0
        mock_cpu_count.return_value = 8
        mock_loadavg.return_value = (1.0, 1.0, 1.0)

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_top_cpu_processes",
            return_value=[
                {
                    "pid": 1,
                    "name": "moderate",
                    "cpu_percent": 30.0,
                    "cmdline": "",
                },
            ],
        ):
            result = provider._get_cpu_recommendations()

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.MEDIUM

    def test_no_processes_returns_empty(self):
        """No high-CPU processes returns empty list."""
        provider = LocalRecommendationProvider()
        with patch.object(provider, "_get_top_cpu_processes", return_value=[]):
            result = provider._get_cpu_recommendations()

        assert result == []


class TestAnalyzeIncidentRouting(SimpleTestCase):
    """Tests for analyze() routing to specific incident analyzers."""

    def test_analyze_routes_to_memory_incident(self):
        """Memory incident routes to _analyze_memory_incident."""
        provider = LocalRecommendationProvider()
        incident = MagicMock()
        incident.title = "High Memory Usage"
        incident.description = "OOM detected"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        with patch.object(provider, "_analyze_memory_incident", return_value=[]) as mock:
            provider.analyze(incident=incident)
            mock.assert_called_once_with(incident)

    def test_analyze_routes_to_cpu_incident(self):
        """CPU incident routes to _analyze_cpu_incident."""
        provider = LocalRecommendationProvider()
        incident = MagicMock()
        incident.title = "High CPU Load"
        incident.description = "Processor at 100%"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        with patch.object(provider, "_analyze_cpu_incident", return_value=[]) as mock:
            provider.analyze(incident=incident)
            mock.assert_called_once_with(incident)

    def test_analyze_routes_to_disk_incident(self):
        """Disk incident routes to _analyze_disk_incident."""
        provider = LocalRecommendationProvider()
        incident = MagicMock()
        incident.title = "Disk Space Low"
        incident.description = "Filesystem running out"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        with patch.object(provider, "_analyze_disk_incident", return_value=[]) as mock:
            provider.analyze(incident=incident)
            mock.assert_called_once_with(incident)


class TestAnalyzeMemoryIncident(SimpleTestCase):
    """Tests for _analyze_memory_incident."""

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_basic_execution(self, mock_iter, mock_vmem):
        """_analyze_memory_incident returns memory recommendations."""
        mock_vmem.return_value = MagicMock(percent=85)
        proc = MagicMock()
        proc.info = {
            "pid": 1,
            "name": "python",
            "memory_percent": 20.0,
            "memory_info": MagicMock(rss=200 * 1024 * 1024),
            "cmdline": ["python", "app.py"],
        }
        mock_iter.return_value = [proc]

        incident = MagicMock()
        incident.id = 42

        provider = LocalRecommendationProvider()
        result = provider._analyze_memory_incident(incident)

        assert len(result) >= 1
        assert result[0].incident_id == 42
        assert result[0].type == RecommendationType.MEMORY


class TestAnalyzeDiskIncident(SimpleTestCase):
    """Tests for _analyze_disk_incident."""

    def test_basic_execution_with_metadata(self):
        """_analyze_disk_incident extracts path from metadata."""
        incident = MagicMock()
        incident.id = 99
        incident.metadata = {"path": "/data"}

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_disk_recommendations",
            return_value=[
                Recommendation(
                    type=RecommendationType.DISK,
                    priority=RecommendationPriority.HIGH,
                    title="Disk",
                    description="Disk issue",
                    incident_id=99,
                )
            ],
        ) as mock_disk:
            result = provider._analyze_disk_incident(incident)
            mock_disk.assert_called_once_with("/data", incident_id=99)

        assert len(result) == 1

    def test_basic_execution_without_metadata(self):
        """_analyze_disk_incident defaults to / when no metadata."""
        incident = MagicMock()
        incident.id = 100
        incident.metadata = None

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_disk_recommendations",
            return_value=[],
        ) as mock_disk:
            provider._analyze_disk_incident(incident)
            mock_disk.assert_called_once_with("/", incident_id=100)

    def test_no_metadata_attribute(self):
        """_analyze_disk_incident handles incident without metadata attr."""
        incident = MagicMock(spec=["id"])
        incident.id = 101

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_disk_recommendations",
            return_value=[],
        ) as mock_disk:
            provider._analyze_disk_incident(incident)
            mock_disk.assert_called_once_with("/", incident_id=101)


class TestAnalyzeCpuIncident(SimpleTestCase):
    """Tests for _analyze_cpu_incident."""

    def test_basic_execution(self):
        """_analyze_cpu_incident delegates to _get_cpu_recommendations."""
        incident = MagicMock()
        incident.id = 77

        provider = LocalRecommendationProvider()
        with patch.object(
            provider,
            "_get_cpu_recommendations",
            return_value=[
                Recommendation(
                    type=RecommendationType.CPU,
                    priority=RecommendationPriority.HIGH,
                    title="CPU",
                    description="CPU issue",
                    incident_id=77,
                )
            ],
        ) as mock_cpu:
            result = provider._analyze_cpu_incident(incident)
            mock_cpu.assert_called_once_with(incident_id=77)

        assert len(result) == 1
        assert result[0].incident_id == 77


class TestGetMemoryRecommendations(SimpleTestCase):
    """Tests for _get_memory_recommendations priority branches."""

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_critical_priority_above_90(self, mock_iter, mock_vmem):
        """Memory > 90% yields CRITICAL priority."""
        mock_vmem.return_value = MagicMock(percent=95)
        proc = MagicMock()
        proc.info = {
            "pid": 1,
            "name": "hog",
            "memory_percent": 40.0,
            "memory_info": MagicMock(rss=400 * 1024 * 1024),
            "cmdline": ["hog"],
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_memory_recommendations(incident_id=1)

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.CRITICAL

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_high_priority_above_80(self, mock_iter, mock_vmem):
        """Memory > 80% yields HIGH priority."""
        mock_vmem.return_value = MagicMock(percent=85)
        proc = MagicMock()
        proc.info = {
            "pid": 2,
            "name": "app",
            "memory_percent": 20.0,
            "memory_info": MagicMock(rss=200 * 1024 * 1024),
            "cmdline": ["app"],
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_memory_recommendations()

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.HIGH

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_medium_priority_above_70(self, mock_iter, mock_vmem):
        """Memory > 70% yields MEDIUM priority."""
        mock_vmem.return_value = MagicMock(percent=75)
        proc = MagicMock()
        proc.info = {
            "pid": 3,
            "name": "svc",
            "memory_percent": 10.0,
            "memory_info": MagicMock(rss=100 * 1024 * 1024),
            "cmdline": ["svc"],
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_memory_recommendations()

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.MEDIUM

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_low_priority_below_70(self, mock_iter, mock_vmem):
        """Memory <= 70% yields LOW priority."""
        mock_vmem.return_value = MagicMock(percent=50)
        proc = MagicMock()
        proc.info = {
            "pid": 4,
            "name": "idle",
            "memory_percent": 5.0,
            "memory_info": MagicMock(rss=50 * 1024 * 1024),
            "cmdline": ["idle"],
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_memory_recommendations()

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.LOW

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_no_processes_returns_empty(self, mock_iter):
        """No qualifying processes returns empty list."""
        mock_iter.return_value = []

        provider = LocalRecommendationProvider()
        result = provider._get_memory_recommendations()

        assert result == []


class TestGetDiskRecommendations(SimpleTestCase):
    """Tests for _get_disk_recommendations priority and fallback branches."""

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_no_issues_returns_health_check(self, mock_usage):
        """No large items or old files returns general health check."""
        mock_usage.return_value = MagicMock(percent=40)

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/")

        assert len(result) == 1
        assert result[0].title == "Disk Health Check"
        assert result[0].priority == RecommendationPriority.LOW

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_oserror_on_disk_usage_gives_medium(self, mock_usage):
        """OSError from disk_usage defaults to MEDIUM priority."""
        mock_usage.side_effect = OSError("not mounted")

        from apps.intelligence.providers.local import LargeFileInfo

        large_item = LargeFileInfo(
            path="/var/log/huge.log",
            size_mb=500.0,
            modified_days_ago=10,
            is_directory=False,
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[large_item]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/mnt")

        assert len(result) == 1
        assert result[0].priority == RecommendationPriority.MEDIUM

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_critical_priority_above_95(self, mock_usage):
        """Disk usage > 95% yields CRITICAL priority."""
        mock_usage.return_value = MagicMock(percent=97)

        from apps.intelligence.providers.local import LargeFileInfo

        large_item = LargeFileInfo(
            path="/var/big",
            size_mb=1000.0,
            modified_days_ago=5,
            is_directory=True,
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[large_item]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/")

        assert result[0].priority == RecommendationPriority.CRITICAL

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_high_priority_above_90(self, mock_usage):
        """Disk usage > 90% yields HIGH priority."""
        mock_usage.return_value = MagicMock(percent=92)

        from apps.intelligence.providers.local import LargeFileInfo

        large_item = LargeFileInfo(
            path="/var/big",
            size_mb=500.0,
            modified_days_ago=5,
            is_directory=False,
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[large_item]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/")

        assert result[0].priority == RecommendationPriority.HIGH

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_medium_priority_above_80(self, mock_usage):
        """Disk usage > 80% yields MEDIUM priority."""
        mock_usage.return_value = MagicMock(percent=85)

        from apps.intelligence.providers.local import LargeFileInfo

        large_item = LargeFileInfo(
            path="/var/big",
            size_mb=200.0,
            modified_days_ago=5,
            is_directory=False,
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[large_item]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/")

        assert result[0].priority == RecommendationPriority.MEDIUM

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_low_priority_below_80(self, mock_usage):
        """Disk usage <= 80% yields LOW priority."""
        mock_usage.return_value = MagicMock(percent=60)

        from apps.intelligence.providers.local import LargeFileInfo

        large_item = LargeFileInfo(
            path="/var/data",
            size_mb=150.0,
            modified_days_ago=5,
            is_directory=False,
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[large_item]):
            with patch.object(provider, "_find_old_logs", return_value=[]):
                result = provider._get_disk_recommendations("/")

        assert result[0].priority == RecommendationPriority.LOW

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_old_files_recommendation(self, mock_usage):
        """Old files generate a separate recommendation."""
        mock_usage.return_value = MagicMock(percent=50)

        from apps.intelligence.providers.local import OldFileInfo

        old_file = OldFileInfo(
            path="/var/log/old.log",
            size_mb=10.0,
            modified_date=datetime.now() - timedelta(days=60),
            days_old=60,
            file_type="log",
        )

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[]):
            with patch.object(provider, "_find_old_logs", return_value=[old_file]):
                result = provider._get_disk_recommendations("/")

        assert len(result) == 1
        assert result[0].title == "Old Logs and Temporary Files Found"
        assert result[0].priority == RecommendationPriority.MEDIUM

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_empty_path_defaults_to_root(self, mock_usage):
        """Empty or whitespace-only path is normalized to '/'."""
        mock_usage.return_value = MagicMock(percent=40)

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[]) as mock_scan:
            with patch.object(provider, "_find_old_logs", return_value=[]):
                provider._get_disk_recommendations("")
                assert mock_scan.call_args[0][0] == "/"

    @patch("apps.intelligence.providers.local.psutil.disk_usage")
    def test_whitespace_path_defaults_to_root(self, mock_usage):
        """Whitespace path is normalized to '/'."""
        mock_usage.return_value = MagicMock(percent=40)

        provider = LocalRecommendationProvider()
        with patch.object(provider, "_scan_large_files", return_value=[]) as mock_scan:
            with patch.object(provider, "_find_old_logs", return_value=[]):
                provider._get_disk_recommendations("   ")
                assert mock_scan.call_args[0][0] == "/"


class TestGetTopMemoryProcessesExceptions(SimpleTestCase):
    """Tests for psutil exceptions in _get_top_memory_processes."""

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_no_such_process_skipped(self, mock_iter):
        """NoSuchProcess exception causes process to be skipped."""
        bad_proc = MagicMock()
        bad_proc.info = MagicMock()
        bad_proc.info.get = MagicMock(side_effect=psutil.NoSuchProcess(pid=1))
        mock_iter.return_value = [bad_proc]

        provider = LocalRecommendationProvider()
        result = provider._get_top_memory_processes()
        assert result == []

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_access_denied_skipped(self, mock_iter):
        """AccessDenied exception causes process to be skipped."""
        bad_proc = MagicMock()
        bad_proc.info = MagicMock()
        bad_proc.info.get = MagicMock(side_effect=psutil.AccessDenied(pid=1))
        mock_iter.return_value = [bad_proc]

        provider = LocalRecommendationProvider()
        result = provider._get_top_memory_processes()
        assert result == []

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_zombie_process_skipped(self, mock_iter):
        """ZombieProcess exception causes process to be skipped."""
        bad_proc = MagicMock()
        bad_proc.info = MagicMock()
        bad_proc.info.get = MagicMock(side_effect=psutil.ZombieProcess(pid=1))
        mock_iter.return_value = [bad_proc]

        provider = LocalRecommendationProvider()
        result = provider._get_top_memory_processes()
        assert result == []

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_process_with_no_mem_info(self, mock_iter):
        """Process with None memory_info gets 0 for mem_mb."""
        proc = MagicMock()
        proc.info = {
            "pid": 50,
            "name": "noinfo",
            "memory_percent": 5.0,
            "memory_info": None,
            "cmdline": None,
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_top_memory_processes()
        assert len(result) == 1
        assert result[0].memory_mb == 0
        assert result[0].name == "noinfo"

    @patch("apps.intelligence.providers.local.psutil.process_iter")
    def test_tiny_process_filtered_out(self, mock_iter):
        """Process with memory_percent <= 0.1 is filtered out."""
        proc = MagicMock()
        proc.info = {
            "pid": 51,
            "name": "tiny",
            "memory_percent": 0.05,
            "memory_info": None,
            "cmdline": [],
        }
        mock_iter.return_value = [proc]

        provider = LocalRecommendationProvider()
        result = provider._get_top_memory_processes()
        assert result == []


class TestDetectIncidentTypeWithAlerts(SimpleTestCase):
    """Tests for _detect_incident_type when alerts have keywords."""

    def test_alert_contains_memory_keyword(self):
        """Alert name/description with memory keyword detected."""
        provider = LocalRecommendationProvider()

        alert = MagicMock()
        alert.name = "RAM Usage Alert"
        alert.description = "Memory is high"

        incident = MagicMock()
        incident.title = "General Alert"
        incident.description = "Something happened"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = [alert]

        result = provider._detect_incident_type(incident)
        assert result == "memory"

    def test_incident_without_alerts_attribute(self):
        """Incident without alerts attribute uses only title/desc."""
        provider = LocalRecommendationProvider()

        incident = MagicMock(spec=["title", "description"])
        incident.title = "General Alert"
        incident.description = "Something happened"

        result = provider._detect_incident_type(incident)
        assert result == "unknown"


class TestGetLocalRecommendations(SimpleTestCase):
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
class TestIntegration(TestCase):
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


class TestBuildDuCommand(SimpleTestCase):
    """Tests for build_du_command — the fast path must run on every platform."""

    def test_gnu_flags_on_linux(self):
        """GNU du gets -ax and --max-depth."""
        with patch("apps.intelligence.providers.local.sys.platform", "linux"):
            cmd = build_du_command("/usr/bin/du", 100, "/var")

        assert cmd[0] == "/usr/bin/du"
        assert "-ax" in cmd
        assert "-k" in cmd
        assert f"--max-depth={SCAN_MAX_DEPTH}" in cmd
        assert cmd[-3:] == ["-t", "100M", "/var"]

    def test_bsd_flags_on_macos(self):
        """BSD du rejects --max-depth, so -d is used instead."""
        with patch("apps.intelligence.providers.local.sys.platform", "darwin"):
            cmd = build_du_command("/usr/bin/du", 100, "/var")

        assert cmd == ["/usr/bin/du", "-k", "-x", "-d", str(SCAN_MAX_DEPTH), "-t", "100M", "/var"]

    def test_bsd_drops_mutually_exclusive_dash_a(self):
        """BSD's synopsis is [-a | -s | -d depth], so -a must not appear with -d."""
        with patch("apps.intelligence.providers.local.sys.platform", "freebsd12"):
            cmd = build_du_command("/usr/bin/du", 50, "/")

        assert "-a" not in cmd
        assert "-ax" not in cmd
        assert not any(arg.startswith("--max-depth") for arg in cmd)


class TestScanBudgetDefaults(SimpleTestCase):
    """A started budget must take its bounds from the module constants."""

    def test_defaults_come_from_module_constants(self):
        with patch("apps.intelligence.providers.local.SCAN_MAX_ENTRIES", 7):
            with patch("apps.intelligence.providers.local.SCAN_TIME_BUDGET_SECONDS", 1.5):
                before = time.monotonic()
                budget = ScanBudget.start()

        assert budget.remaining_entries == 7
        assert 1.0 <= budget.deadline - before <= 2.0

    def test_du_timeout_stays_a_small_gamble(self):
        assert DU_TIMEOUT_SECONDS < SCAN_TIME_BUDGET_SECONDS


class TestIterBounded(SimpleTestCase):
    """Tests for iter_bounded — the bounded replacement for rglob('*')."""

    def test_unreadable_directory_is_skipped(self):
        """A directory that raises on iterdir is skipped, not propagated."""
        directory = _mock_dir(iterdir_error=PermissionError("denied"))

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == []

    def test_entry_stat_error_stops_descent_only(self):
        """An entry whose is_dir() raises is yielded but not descended into."""
        entry = _mock_file("/mock/entry")
        entry.is_dir.side_effect = OSError("stale handle")
        directory = _mock_dir([entry])

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == [entry]

    def test_entry_cap_stops_iteration(self):
        """The entry cap returns what was found rather than continuing."""
        entries = [_mock_file(f"/mock/{index}") for index in range(5)]
        directory = _mock_dir(entries)

        budget = ScanBudget(deadline=time.monotonic() + 30, remaining_entries=2)
        result = list(iter_bounded(directory, budget))

        assert len(result) == 2

    def test_the_cap_stops_before_the_directory_is_fully_produced(self):
        """A huge directory is consumed lazily: the cap stops the iterator early.

        Materializing the directory first spends the time and memory the caps
        exist to bound before either cap is ever consulted — and this provider
        now runs synchronously on an operator's terminal, so a directory with a
        million entries would block the command while the list was built.
        """
        produced = 0

        def endless():
            nonlocal produced
            for index in range(10_000):
                produced += 1
                yield _mock_file(f"/mock/{index}")

        directory = _mock_dir()
        directory.iterdir.side_effect = lambda: endless()

        budget = ScanBudget(deadline=time.monotonic() + 30, remaining_entries=3)
        result = list(iter_bounded(directory, budget))

        assert len(result) == 3
        # One more than the cap: the entry whose arrival the budget refuses.
        assert produced <= 4

    def test_a_directory_that_fails_mid_iteration_is_skipped_not_raised(self):
        """iterdir() can raise while producing, not only when it is called."""

        def fails_after_one():
            yield _mock_file("/mock/first")
            raise PermissionError("denied partway through")

        directory = _mock_dir()
        directory.iterdir.side_effect = lambda: fails_after_one()

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert [str(entry) for entry in result] == ["/mock/first"]

    def test_exhausted_budget_stops_before_reading_directory(self):
        """An already-exhausted budget yields nothing and reads no directory."""
        directory = _mock_dir()

        budget = ScanBudget(deadline=time.monotonic() + 30, remaining_entries=0)
        result = list(iter_bounded(directory, budget))

        assert result == []
        directory.iterdir.assert_not_called()

    def test_entry_on_another_filesystem_is_skipped(self):
        """iter_bounded carries du -x: entries off the root device are skipped."""
        same = _mock_file("/root/same")
        other = _mock_file("/root/other", st_dev=MOCK_DEV + 1)
        directory = _mock_dir([same, other])

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == [same]

    def test_directory_on_another_filesystem_is_not_descended(self):
        """A /proc-like mount is neither yielded nor walked into."""
        child = _mock_file("/proc/kcore", size=140_737_477_881_856)
        procfs = _mock_dir([child], st_dev=MOCK_DEV + 1)
        procfs.is_file.return_value = False
        directory = _mock_dir([procfs])

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == []
        procfs.iterdir.assert_not_called()

    def test_unstattable_root_yields_nothing(self):
        """If the root device cannot be determined, nothing is walked."""
        directory = _mock_dir()
        directory.stat.side_effect = PermissionError("denied")

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == []
        directory.iterdir.assert_not_called()

    def test_entry_that_cannot_be_stat_ed_is_skipped(self):
        """A vanished or unreadable entry is skipped, not yielded."""
        broken = _mock_file("/root/broken")
        broken.stat.side_effect = OSError("vanished")
        directory = _mock_dir([broken])

        result = list(iter_bounded(directory, ScanBudget.start()))

        assert result == []

    def test_symlinked_directory_is_not_followed(self):
        """Symlinked directories are yielded but never descended into."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "real").mkdir()
            (root / "real" / "inner.txt").write_text("x")
            (root / "link").symlink_to(root / "real")

            result = [p.name for p in iter_bounded(root, ScanBudget.start())]

        assert "link" in result
        assert result.count("inner.txt") == 1


def _build_deep_tree(root: Path, size_bytes: int = 4096) -> None:
    """Create root/f1 .. root/l1/l2/l3/f4 — deeper than SCAN_MAX_DEPTH."""
    payload = b"x" * size_bytes
    (root / "f1.bin").write_bytes(payload)
    level = root
    for depth in range(2, 6):
        level = level / f"l{depth - 1}"
        level.mkdir()
        (level / f"f{depth}.bin").write_bytes(payload)


class TestBoundedFallbackScan(SimpleTestCase):
    """The Python fallback must stay bounded when du is unavailable."""

    def _provider(self, **kwargs):
        return LocalRecommendationProvider(large_file_threshold_mb=0.001, **kwargs)

    @patch("apps.intelligence.providers.local.shutil.which", return_value=None)
    def test_missing_du_falls_back_and_stops_at_depth_limit(self, _mock_which):
        """No du on PATH: the fallback runs and never descends past the limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _build_deep_tree(root)

            result = self._provider()._scan_large_files(root.as_posix())

        found = {Path(item.path).name for item in result}
        assert "f1.bin" in found
        assert "f3.bin" in found
        assert "f4.bin" not in found
        assert "f5.bin" not in found

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_failing_du_falls_back_and_stops_at_depth_limit(self, mock_run):
        """A du that exits non-zero (BSD rejecting a flag) still yields a bounded result."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _build_deep_tree(root)

            result = self._provider()._scan_large_files(root.as_posix())

        found = {Path(item.path).name for item in result}
        assert found == {"f1.bin", "f2.bin", "f3.bin"}

    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_timed_out_du_falls_back_and_stops_at_depth_limit(self, mock_run):
        """A du that times out still yields a bounded result rather than hanging."""
        mock_run.side_effect = TimeoutExpired(cmd=["du"], timeout=DU_TIMEOUT_SECONDS)  # nosemgrep

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _build_deep_tree(root)

            result = self._provider()._scan_large_files(root.as_posix())

        assert {Path(item.path).name for item in result} == {"f1.bin", "f2.bin", "f3.bin"}

    @patch("apps.intelligence.providers.local.shutil.which", return_value=None)
    def test_fallback_stops_at_file_cap(self, _mock_which):
        """The entry cap returns what was found instead of walking on."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index in range(10):
                (root / f"big{index}.bin").write_bytes(b"x" * 4096)

            # Patch the module constant, not ScanBudget.start: the cap the
            # operator actually gets must come from SCAN_MAX_ENTRIES.
            with patch("apps.intelligence.providers.local.SCAN_MAX_ENTRIES", 3):
                result = self._provider()._scan_large_files(root.as_posix())

        assert len(result) == 3

    @patch("apps.intelligence.providers.local.shutil.which", return_value=None)
    def test_fallback_respects_time_budget(self, _mock_which):
        """When the wall-clock budget expires, partial results are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index in range(10):
                (root / f"big{index}.bin").write_bytes(b"x" * 4096)

            # start() -> 0.0, then two in-budget checks, then past the deadline.
            clock = itertools.chain([0.0, 0.0, 0.0], itertools.repeat(100.0))
            with patch(
                "apps.intelligence.providers.local.time.monotonic",
                side_effect=lambda: next(clock),
            ):
                result = self._provider()._scan_large_files(root.as_posix())

        assert len(result) == 1


class TestSingleFilesystemScan(SimpleTestCase):
    """The fallback must not wander off the scanned filesystem."""

    @patch("apps.intelligence.providers.local.shutil.which", return_value=None)
    def test_pseudo_filesystem_file_is_not_recommended(self, _mock_which):
        """/proc/kcore-shaped entries never become the top recommendation."""
        kcore = _mock_file(
            "/proc/kcore",
            size=140_737_477_881_856,
            st_dev=MOCK_DEV + 1,
        )
        real = _mock_file("/bigfile.bin", size=200 * 1024 * 1024)
        scan_path = _mock_dir([kcore, real])

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=scan_path,
        ):
            result = LocalRecommendationProvider()._scan_large_files("/")

        assert [item.path for item in result] == ["/bigfile.bin"]


class TestOnePerAnalysisBudget(SimpleTestCase):
    """One budget covers a whole disk analysis, du included."""

    @patch("apps.intelligence.providers.local.shutil.which", return_value=None)
    def test_disk_recommendations_open_exactly_one_budget(self, _mock_which):
        """Large-file scan and old-log scan share the operator's single wait."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "f.bin").write_bytes(b"x" * 4096)

            with patch(
                "apps.intelligence.providers.local.ScanBudget.start",
                side_effect=ScanBudget.start,
            ) as mock_start:
                provider = LocalRecommendationProvider(scan_paths=[root.as_posix()])
                provider._get_disk_recommendations(root.as_posix())

        assert mock_start.call_count == 1

    @patch("apps.intelligence.providers.local.shutil.which", return_value="/usr/bin/du")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_timeout_is_clamped_to_the_remaining_budget(self, mock_run, _mock_which):
        """du may not outlive the analysis budget it is spending."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        budget = ScanBudget(deadline=time.monotonic() + 1.0, remaining_entries=100)

        LocalRecommendationProvider()._scan_large_files("/var", budget=budget)

        assert 0 < mock_run.call_args.kwargs["timeout"] <= 1.0

    @patch("apps.intelligence.providers.local.shutil.which", return_value="/usr/bin/du")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_is_skipped_once_the_budget_is_gone(self, mock_run, _mock_which):
        """An exhausted budget spends nothing more on the du gamble."""
        budget = ScanBudget(deadline=time.monotonic() - 1.0, remaining_entries=100)

        result = LocalRecommendationProvider()._scan_large_files("/var", budget=budget)

        assert result == []
        mock_run.assert_not_called()

    @patch("apps.intelligence.providers.local.os.path.isdir", return_value=False)
    @patch("apps.intelligence.providers.local.os.stat")
    @patch("apps.intelligence.providers.local.shutil.which", return_value="/usr/bin/du")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_du_parse_loop_is_bounded_by_entries(
        self, mock_run, _mock_which, mock_stat, _mock_isdir
    ):
        """The du success path is capped too, not just the fallback."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="".join(f"{204800 + index}\t/var/f{index}\n" for index in range(5)),
        )
        mock_stat.return_value = MagicMock(st_mtime=datetime.now().timestamp())
        budget = ScanBudget(deadline=time.monotonic() + 30, remaining_entries=2)

        result = LocalRecommendationProvider()._scan_large_files("/var", budget=budget)

        assert len(result) == 2

    @patch("apps.intelligence.providers.local.shutil.which")
    @patch("apps.intelligence.providers.local.subprocess.run")
    def test_resolved_du_path_reaches_subprocess(self, mock_run, mock_which):
        """argv[0] is the shutil.which result, never a bare name."""
        mock_which.return_value = "/opt/homebrew/bin/du"
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        LocalRecommendationProvider()._scan_large_files("/var")

        assert mock_run.call_args[0][0][0] == "/opt/homebrew/bin/du"


class TestBoundedOldLogScan(SimpleTestCase):
    """_find_old_logs shares the same bounds — it also walked rglob('*')."""

    def _make_old(self, path: Path) -> None:
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        old = (datetime.now() - timedelta(days=60)).timestamp()
        os.utime(path, (old, old))

    def test_stops_at_depth_limit(self):
        """Old files deeper than the limit are not reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_old(root / "shallow.log")
            deep = root / "a" / "b" / "c" / "d"
            deep.mkdir(parents=True)
            self._make_old(deep / "deep.log")

            provider = LocalRecommendationProvider(scan_paths=[root.as_posix()])
            result = provider._find_old_logs()

        found = {Path(item.path).name for item in result}
        assert found == {"shallow.log"}

    def test_exhausted_budget_skips_remaining_scan_dirs(self):
        """One budget covers every scan dir; later dirs are skipped once it is gone."""
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self._make_old(Path(first) / "one.log")
            self._make_old(Path(second) / "two.log")

            with patch("apps.intelligence.providers.local.SCAN_MAX_ENTRIES", 1):
                provider = LocalRecommendationProvider(scan_paths=[first, second])
                result = provider._find_old_logs()

        assert len(result) == 1

    def test_scan_dir_oserror_is_skipped(self):
        """An OSError while resolving a scan dir is caught, not raised."""
        mock_path = MagicMock()
        mock_path.exists.side_effect = OSError("io error")

        with patch(
            "apps.intelligence.providers.local.Path.expanduser",
            return_value=mock_path,
        ):
            provider = LocalRecommendationProvider(scan_paths=["/var/log"])
            result = provider._find_old_logs()

        assert result == []
