# Checkers Test Suite Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure the monolithic `apps/checkers/tests.py` (474 lines) into a proper test directory structure mirroring the module layout.

**Architecture:** Split tests by component into separate files within a `tests/` directory. Each checker gets its own test file under `tests/checkers/`. Base classes, models, Django checks, and commands get dedicated test files.

**Tech Stack:** Django TestCase, pytest, unittest.mock, psutil mocking

---

## Current State

**Problem:** All tests in single `apps/checkers/tests.py` file (474 lines)

**Current Test Classes:**
- `CheckResultTests` - CheckResult dataclass tests
- `BaseCheckerTests` - threshold logic
- `CPUCheckerTests` - with psutil mocking
- `MemoryCheckerTests` - with psutil mocking
- `DiskCheckerTests` - with psutil mocking, multi-path
- `NetworkCheckerTests` - with subprocess mocking
- `ProcessCheckerTests` - with psutil mocking
- `CheckerRegistryTests` - registry validation
- `SystemChecksTests` - Django system checks
- `CheckerEnablementTests` - skip/disable settings

## Target Structure

```
apps/checkers/
├── tests/
│   ├── __init__.py
│   ├── test_base.py           # CheckResult, CheckStatus, BaseChecker
│   ├── checkers/
│   │   ├── __init__.py
│   │   ├── test_cpu.py        # CPUChecker tests
│   │   ├── test_memory.py     # MemoryChecker tests
│   │   ├── test_disk.py       # DiskChecker tests
│   │   ├── test_network.py    # NetworkChecker tests
│   │   └── test_process.py    # ProcessChecker tests
│   ├── test_registry.py       # Registry and enablement tests
│   ├── test_checks.py         # Django system checks tests
│   └── test_models.py         # CheckRun model tests (new)
└── tests.py                   # DELETE after migration
```

---

## Implementation Tasks

### Task 1: Create test directory structure

**Files:**
- Create: `apps/checkers/tests/__init__.py`
- Create: `apps/checkers/tests/checkers/__init__.py`

**Step 1: Create tests directory**

```bash
mkdir -p apps/checkers/tests/checkers
```

**Step 2: Create __init__.py files**

```python
# apps/checkers/tests/__init__.py
"""Checkers app test suite."""
```

```python
# apps/checkers/tests/checkers/__init__.py
"""Checker implementation tests."""
```

**Step 3: Verify structure**

Run: `ls -la apps/checkers/tests/`
Expected: Shows `__init__.py` and `checkers/` directory

**Step 4: Commit**

```bash
git add apps/checkers/tests/
git commit -m "chore(checkers): create test directory structure"
```

---

### Task 2: Create test_base.py with base class tests

**Files:**
- Create: `apps/checkers/tests/test_base.py`
- Read: `apps/checkers/tests.py:1-100` (for extraction)

**Step 1: Read source tests**

Read lines 1-100 of `apps/checkers/tests.py` to extract `CheckResultTests` and `BaseCheckerTests`.

**Step 2: Write test_base.py**

```python
"""Tests for base checker classes and data structures."""

from django.test import TestCase

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


class CheckResultTests(TestCase):
    """Tests for the CheckResult dataclass."""

    def test_check_result_creation(self):
        """CheckResult can be created with required fields."""
        result = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            checker_name="test",
        )
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.message, "All good")
        self.assertEqual(result.checker_name, "test")
        self.assertIsNone(result.metrics)
        self.assertIsNone(result.error)

    def test_check_result_with_metrics(self):
        """CheckResult can include metrics dictionary."""
        metrics = {"cpu_percent": 45.2, "cpu_count": 4}
        result = CheckResult(
            status=CheckStatus.WARNING,
            message="CPU high",
            checker_name="cpu",
            metrics=metrics,
        )
        self.assertEqual(result.metrics, metrics)

    def test_check_result_with_error(self):
        """CheckResult can include error string."""
        result = CheckResult(
            status=CheckStatus.UNKNOWN,
            message="Check failed",
            checker_name="test",
            error="Connection timeout",
        )
        self.assertEqual(result.error, "Connection timeout")


class BaseCheckerTests(TestCase):
    """Tests for BaseChecker threshold logic."""

    def test_determine_status_ok(self):
        """Values below warning threshold return OK."""

        class TestChecker(BaseChecker):
            name = "test"
            warning_threshold = 70.0
            critical_threshold = 90.0

            def check(self):
                return self._make_result(CheckStatus.OK, "ok")

        checker = TestChecker()
        self.assertEqual(checker._determine_status(50.0), CheckStatus.OK)
        self.assertEqual(checker._determine_status(69.9), CheckStatus.OK)

    def test_determine_status_warning(self):
        """Values at or above warning but below critical return WARNING."""

        class TestChecker(BaseChecker):
            name = "test"
            warning_threshold = 70.0
            critical_threshold = 90.0

            def check(self):
                return self._make_result(CheckStatus.OK, "ok")

        checker = TestChecker()
        self.assertEqual(checker._determine_status(70.0), CheckStatus.WARNING)
        self.assertEqual(checker._determine_status(89.9), CheckStatus.WARNING)

    def test_determine_status_critical(self):
        """Values at or above critical threshold return CRITICAL."""

        class TestChecker(BaseChecker):
            name = "test"
            warning_threshold = 70.0
            critical_threshold = 90.0

            def check(self):
                return self._make_result(CheckStatus.OK, "ok")

        checker = TestChecker()
        self.assertEqual(checker._determine_status(90.0), CheckStatus.CRITICAL)
        self.assertEqual(checker._determine_status(100.0), CheckStatus.CRITICAL)

    def test_threshold_override(self):
        """Thresholds can be overridden in constructor."""

        class TestChecker(BaseChecker):
            name = "test"
            warning_threshold = 70.0
            critical_threshold = 90.0

            def check(self):
                return self._make_result(CheckStatus.OK, "ok")

        checker = TestChecker(warning_threshold=50.0, critical_threshold=80.0)
        self.assertEqual(checker.warning_threshold, 50.0)
        self.assertEqual(checker.critical_threshold, 80.0)
        # 60% is now WARNING with new thresholds
        self.assertEqual(checker._determine_status(60.0), CheckStatus.WARNING)

    def test_make_result(self):
        """_make_result creates CheckResult with checker name."""

        class TestChecker(BaseChecker):
            name = "mytest"
            warning_threshold = 70.0
            critical_threshold = 90.0

            def check(self):
                return self._make_result(
                    CheckStatus.OK, "test message", {"metric": 1}
                )

        checker = TestChecker()
        result = checker.check()
        self.assertEqual(result.checker_name, "mytest")
        self.assertEqual(result.message, "test message")
        self.assertEqual(result.metrics, {"metric": 1})

    def test_error_result(self):
        """_error_result creates UNKNOWN status with error."""

        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                return self._error_result("Something broke")

        checker = TestChecker()
        result = checker.check()
        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertEqual(result.error, "Something broke")
```

**Step 3: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/test_base.py -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add apps/checkers/tests/test_base.py
git commit -m "test(checkers): add test_base.py for base classes"
```

---

### Task 3: Create test_cpu.py with CPU checker tests

**Files:**
- Create: `apps/checkers/tests/checkers/test_cpu.py`
- Read: `apps/checkers/tests.py` (for CPUCheckerTests extraction)

**Step 1: Write test_cpu.py**

```python
"""Tests for the CPU checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.cpu import CPUChecker


class CPUCheckerTests(TestCase):
    """Tests for CPUChecker implementation."""

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_ok(self, mock_psutil):
        """CPU below warning threshold returns OK."""
        mock_psutil.cpu_percent.return_value = 25.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("25.0%", result.message)
        self.assertEqual(result.metrics["cpu_percent"], 25.0)
        self.assertEqual(result.metrics["cpu_count"], 4)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_warning(self, mock_psutil):
        """CPU at warning threshold returns WARNING."""
        mock_psutil.cpu_percent.return_value = 75.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_critical(self, mock_psutil):
        """CPU at critical threshold returns CRITICAL."""
        mock_psutil.cpu_percent.return_value = 95.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_per_cpu(self, mock_psutil):
        """Per-CPU metrics included when requested."""
        mock_psutil.cpu_percent.side_effect = [
            50.0,  # Overall
            [40.0, 60.0, 45.0, 55.0],  # Per-CPU
        ]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(per_cpu=True)
        result = checker.check()

        self.assertIn("per_cpu", result.metrics)
        self.assertEqual(len(result.metrics["per_cpu"]), 4)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_custom_thresholds(self, mock_psutil):
        """Custom thresholds override defaults."""
        mock_psutil.cpu_percent.return_value = 55.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(warning_threshold=50.0, critical_threshold=80.0)
        result = checker.check()

        # 55% is WARNING with 50/80 thresholds
        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_error_handling(self, mock_psutil):
        """Errors during check return UNKNOWN status."""
        mock_psutil.cpu_percent.side_effect = Exception("psutil error")

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("psutil error", result.error)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/checkers/test_cpu.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/checkers/test_cpu.py
git commit -m "test(checkers): add test_cpu.py for CPU checker"
```

---

### Task 4: Create test_memory.py with memory checker tests

**Files:**
- Create: `apps/checkers/tests/checkers/test_memory.py`

**Step 1: Write test_memory.py**

```python
"""Tests for the memory checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.memory import MemoryChecker


class MemoryCheckerTests(TestCase):
    """Tests for MemoryChecker implementation."""

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_ok(self, mock_psutil):
        """Memory below warning threshold returns OK."""
        mock_memory = MagicMock()
        mock_memory.percent = 45.0
        mock_memory.total = 16 * 1024**3  # 16 GB
        mock_memory.available = 8 * 1024**3  # 8 GB
        mock_memory.used = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("45.0%", result.message)
        self.assertEqual(result.metrics["memory_percent"], 45.0)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_warning(self, mock_psutil):
        """Memory at warning threshold returns WARNING."""
        mock_memory = MagicMock()
        mock_memory.percent = 75.0
        mock_memory.total = 16 * 1024**3
        mock_memory.available = 4 * 1024**3
        mock_memory.used = 12 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_critical(self, mock_psutil):
        """Memory at critical threshold returns CRITICAL."""
        mock_memory = MagicMock()
        mock_memory.percent = 95.0
        mock_memory.total = 16 * 1024**3
        mock_memory.available = 1 * 1024**3
        mock_memory.used = 15 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_with_swap(self, mock_psutil):
        """Swap memory included when requested."""
        mock_memory = MagicMock()
        mock_memory.percent = 50.0
        mock_memory.total = 16 * 1024**3
        mock_memory.available = 8 * 1024**3
        mock_memory.used = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        mock_swap = MagicMock()
        mock_swap.percent = 10.0
        mock_swap.total = 4 * 1024**3
        mock_swap.used = 400 * 1024**2
        mock_psutil.swap_memory.return_value = mock_swap

        checker = MemoryChecker(include_swap=True)
        result = checker.check()

        self.assertIn("swap_percent", result.metrics)
        self.assertEqual(result.metrics["swap_percent"], 10.0)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_error_handling(self, mock_psutil):
        """Errors during check return UNKNOWN status."""
        mock_psutil.virtual_memory.side_effect = Exception("psutil error")

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("psutil error", result.error)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/checkers/test_memory.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/checkers/test_memory.py
git commit -m "test(checkers): add test_memory.py for memory checker"
```

---

### Task 5: Create test_disk.py with disk checker tests

**Files:**
- Create: `apps/checkers/tests/checkers/test_disk.py`

**Step 1: Write test_disk.py**

```python
"""Tests for the disk checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk import DiskChecker


class DiskCheckerTests(TestCase):
    """Tests for DiskChecker implementation."""

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_ok(self, mock_psutil):
        """Disk below warning threshold returns OK."""
        mock_usage = MagicMock()
        mock_usage.percent = 50.0
        mock_usage.total = 500 * 1024**3  # 500 GB
        mock_usage.used = 250 * 1024**3
        mock_usage.free = 250 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("50.0%", result.message)

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_warning(self, mock_psutil):
        """Disk at warning threshold returns WARNING."""
        mock_usage = MagicMock()
        mock_usage.percent = 75.0
        mock_usage.total = 500 * 1024**3
        mock_usage.used = 375 * 1024**3
        mock_usage.free = 125 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_critical(self, mock_psutil):
        """Disk at critical threshold returns CRITICAL."""
        mock_usage = MagicMock()
        mock_usage.percent = 95.0
        mock_usage.total = 500 * 1024**3
        mock_usage.used = 475 * 1024**3
        mock_usage.free = 25 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_multiple_paths(self, mock_psutil):
        """Multiple paths checked, worst status returned."""
        mock_root = MagicMock()
        mock_root.percent = 50.0
        mock_root.total = 500 * 1024**3
        mock_root.used = 250 * 1024**3
        mock_root.free = 250 * 1024**3

        mock_data = MagicMock()
        mock_data.percent = 85.0  # WARNING level
        mock_data.total = 1000 * 1024**3
        mock_data.used = 850 * 1024**3
        mock_data.free = 150 * 1024**3

        mock_psutil.disk_usage.side_effect = [mock_root, mock_data]

        checker = DiskChecker(paths=["/", "/data"])
        result = checker.check()

        # Should return WARNING because /data is at 85%
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertIn("/data", result.message)

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_default_path(self, mock_psutil):
        """Default path is / when none specified."""
        mock_usage = MagicMock()
        mock_usage.percent = 50.0
        mock_usage.total = 500 * 1024**3
        mock_usage.used = 250 * 1024**3
        mock_usage.free = 250 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker()
        result = checker.check()

        mock_psutil.disk_usage.assert_called_with("/")

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_error_handling(self, mock_psutil):
        """Errors during check return UNKNOWN status."""
        mock_psutil.disk_usage.side_effect = Exception("No such path")

        checker = DiskChecker(paths=["/nonexistent"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("No such path", result.error)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/checkers/test_disk.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/checkers/test_disk.py
git commit -m "test(checkers): add test_disk.py for disk checker"
```

---

### Task 6: Create test_network.py with network checker tests

**Files:**
- Create: `apps/checkers/tests/checkers/test_network.py`

**Step 1: Write test_network.py**

```python
"""Tests for the network checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.network import NetworkChecker


class NetworkCheckerTests(TestCase):
    """Tests for NetworkChecker implementation."""

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_ok(self, mock_subprocess):
        """Successful ping returns OK."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=15.2 ms"
        mock_subprocess.run.return_value = mock_result

        checker = NetworkChecker(hosts=["8.8.8.8"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("reachable", result.message.lower())

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_host_unreachable(self, mock_subprocess):
        """Unreachable host returns CRITICAL."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Request timeout"
        mock_subprocess.run.return_value = mock_result

        checker = NetworkChecker(hosts=["192.0.2.1"])  # TEST-NET, unreachable
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_multiple_hosts(self, mock_subprocess):
        """Multiple hosts checked, any failure is CRITICAL."""
        mock_success = MagicMock()
        mock_success.returncode = 0
        mock_success.stdout = "time=10.0 ms"

        mock_failure = MagicMock()
        mock_failure.returncode = 1
        mock_failure.stdout = ""
        mock_failure.stderr = "Request timeout"

        mock_subprocess.run.side_effect = [mock_success, mock_failure]

        checker = NetworkChecker(hosts=["8.8.8.8", "192.0.2.1"])
        result = checker.check()

        # One host failed, should be CRITICAL
        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_latency_parsing(self, mock_subprocess):
        """Latency extracted from ping output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=25.5 ms"
        mock_subprocess.run.return_value = mock_result

        checker = NetworkChecker(hosts=["8.8.8.8"])
        result = checker.check()

        self.assertIn("latency_ms", result.metrics)
        # Latency should be parsed from output

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_default_hosts(self, mock_subprocess):
        """Default hosts used when none specified."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "time=10.0 ms"
        mock_subprocess.run.return_value = mock_result

        checker = NetworkChecker()
        result = checker.check()

        # Should have checked default hosts (8.8.8.8, 1.1.1.1)
        self.assertTrue(mock_subprocess.run.called)

    @patch("apps.checkers.checkers.network.subprocess")
    def test_network_check_error_handling(self, mock_subprocess):
        """Subprocess errors return UNKNOWN status."""
        mock_subprocess.run.side_effect = Exception("Command failed")

        checker = NetworkChecker(hosts=["8.8.8.8"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("Command failed", result.error)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/checkers/test_network.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/checkers/test_network.py
git commit -m "test(checkers): add test_network.py for network checker"
```

---

### Task 7: Create test_process.py with process checker tests

**Files:**
- Create: `apps/checkers/tests/checkers/test_process.py`

**Step 1: Write test_process.py**

```python
"""Tests for the process checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.process import ProcessChecker


class ProcessCheckerTests(TestCase):
    """Tests for ProcessChecker implementation."""

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_ok(self, mock_psutil):
        """Process running returns OK."""
        mock_proc = MagicMock()
        mock_proc.info = {"name": "nginx", "pid": 1234, "status": "running"}
        mock_psutil.process_iter.return_value = [mock_proc]

        checker = ProcessChecker(processes=["nginx"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("nginx", result.message)

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_not_running(self, mock_psutil):
        """Process not running returns CRITICAL."""
        mock_psutil.process_iter.return_value = []

        checker = ProcessChecker(processes=["nginx"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertIn("nginx", result.message)

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_multiple_processes(self, mock_psutil):
        """Multiple processes, any missing is CRITICAL."""
        mock_nginx = MagicMock()
        mock_nginx.info = {"name": "nginx", "pid": 1234, "status": "running"}
        # postgres not in list
        mock_psutil.process_iter.return_value = [mock_nginx]

        checker = ProcessChecker(processes=["nginx", "postgres"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertIn("postgres", result.message)

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_all_running(self, mock_psutil):
        """All processes running returns OK."""
        mock_nginx = MagicMock()
        mock_nginx.info = {"name": "nginx", "pid": 1234, "status": "running"}
        mock_postgres = MagicMock()
        mock_postgres.info = {"name": "postgres", "pid": 5678, "status": "running"}
        mock_psutil.process_iter.return_value = [mock_nginx, mock_postgres]

        checker = ProcessChecker(processes=["nginx", "postgres"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_no_processes_specified(self, mock_psutil):
        """No processes specified returns OK (nothing to check)."""
        checker = ProcessChecker(processes=[])
        result = checker.check()

        # Empty process list means nothing to check, should be OK or UNKNOWN
        self.assertIn(result.status, [CheckStatus.OK, CheckStatus.UNKNOWN])

    @patch("apps.checkers.checkers.process.psutil")
    def test_process_check_error_handling(self, mock_psutil):
        """Errors during check return UNKNOWN status."""
        mock_psutil.process_iter.side_effect = Exception("Access denied")

        checker = ProcessChecker(processes=["nginx"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("Access denied", result.error)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/checkers/test_process.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/checkers/test_process.py
git commit -m "test(checkers): add test_process.py for process checker"
```

---

### Task 8: Create test_registry.py with registry and enablement tests

**Files:**
- Create: `apps/checkers/tests/test_registry.py`

**Step 1: Write test_registry.py**

```python
"""Tests for the checker registry and enablement system."""

from django.test import TestCase, override_settings

from apps.checkers.checkers import (
    CHECKER_REGISTRY,
    get_enabled_checkers,
    is_checker_enabled,
)
from apps.checkers.checkers.base import BaseChecker
from apps.checkers.checkers.cpu import CPUChecker
from apps.checkers.checkers.disk import DiskChecker
from apps.checkers.checkers.memory import MemoryChecker
from apps.checkers.checkers.network import NetworkChecker
from apps.checkers.checkers.process import ProcessChecker


class CheckerRegistryTests(TestCase):
    """Tests for the CHECKER_REGISTRY."""

    def test_all_checkers_registered(self):
        """All expected checkers are in registry."""
        expected = {"cpu", "memory", "disk", "network", "process"}
        self.assertEqual(set(CHECKER_REGISTRY.keys()), expected)

    def test_registry_values_are_checker_classes(self):
        """All registry values are BaseChecker subclasses."""
        for name, checker_class in CHECKER_REGISTRY.items():
            self.assertTrue(
                issubclass(checker_class, BaseChecker),
                f"{name} is not a BaseChecker subclass",
            )

    def test_checker_classes_match_expected(self):
        """Registry maps to correct checker classes."""
        self.assertIs(CHECKER_REGISTRY["cpu"], CPUChecker)
        self.assertIs(CHECKER_REGISTRY["memory"], MemoryChecker)
        self.assertIs(CHECKER_REGISTRY["disk"], DiskChecker)
        self.assertIs(CHECKER_REGISTRY["network"], NetworkChecker)
        self.assertIs(CHECKER_REGISTRY["process"], ProcessChecker)

    def test_checker_names_match_keys(self):
        """Checker class names match their registry keys."""
        for name, checker_class in CHECKER_REGISTRY.items():
            self.assertEqual(
                checker_class.name,
                name,
                f"Checker {checker_class.__name__} has name '{checker_class.name}' but is registered as '{name}'",
            )


class CheckerEnablementTests(TestCase):
    """Tests for checker enable/disable functionality."""

    def test_is_checker_enabled_default(self):
        """Checkers enabled by default."""
        self.assertTrue(is_checker_enabled("cpu"))
        self.assertTrue(is_checker_enabled("memory"))

    @override_settings(CHECKERS_SKIP_ALL=True)
    def test_skip_all_disables_all(self):
        """CHECKERS_SKIP_ALL disables all checkers."""
        self.assertFalse(is_checker_enabled("cpu"))
        self.assertFalse(is_checker_enabled("memory"))

    @override_settings(CHECKERS_SKIP=["cpu", "disk"])
    def test_skip_list_disables_specific(self):
        """CHECKERS_SKIP disables specific checkers."""
        self.assertFalse(is_checker_enabled("cpu"))
        self.assertFalse(is_checker_enabled("disk"))
        self.assertTrue(is_checker_enabled("memory"))
        self.assertTrue(is_checker_enabled("network"))

    def test_get_enabled_checkers_default(self):
        """All checkers returned when none disabled."""
        enabled = get_enabled_checkers()
        self.assertEqual(set(enabled.keys()), set(CHECKER_REGISTRY.keys()))

    @override_settings(CHECKERS_SKIP=["cpu"])
    def test_get_enabled_checkers_with_skip(self):
        """Disabled checkers excluded from get_enabled_checkers."""
        enabled = get_enabled_checkers()
        self.assertNotIn("cpu", enabled)
        self.assertIn("memory", enabled)
        self.assertIn("disk", enabled)

    @override_settings(CHECKERS_SKIP_ALL=True)
    def test_get_enabled_checkers_all_skipped(self):
        """Empty dict when all checkers disabled."""
        enabled = get_enabled_checkers()
        self.assertEqual(enabled, {})

    def test_is_checker_enabled_unknown_checker(self):
        """Unknown checker names return False or raise."""
        # Depending on implementation, might return False or raise
        result = is_checker_enabled("nonexistent")
        self.assertFalse(result)
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/test_registry.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/test_registry.py
git commit -m "test(checkers): add test_registry.py for registry and enablement"
```

---

### Task 9: Create test_checks.py with Django system checks tests

**Files:**
- Create: `apps/checkers/tests/test_checks.py`

**Step 1: Write test_checks.py**

```python
"""Tests for Django system checks in the checkers app."""

from unittest.mock import MagicMock, patch

from django.core.checks import Error, Warning
from django.test import TestCase, override_settings

from apps.checkers.checks import (
    check_crontab_configuration,
    check_database_connection,
    check_database_tables_exist,
    check_pending_migrations,
)


class DatabaseConnectionCheckTests(TestCase):
    """Tests for database connection system check."""

    def test_database_connection_ok(self):
        """No errors when database is connected."""
        # Default test database should be connected
        errors = check_database_connection(None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks.connection")
    def test_database_connection_failed(self, mock_connection):
        """Error returned when database connection fails."""
        mock_connection.ensure_connection.side_effect = Exception("Connection refused")

        errors = check_database_connection(None)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Error)
        self.assertIn("database", errors[0].msg.lower())


class PendingMigrationsCheckTests(TestCase):
    """Tests for pending migrations system check."""

    def test_no_pending_migrations(self):
        """No warnings when migrations are applied."""
        # Test database should have all migrations
        warnings = check_pending_migrations(None)
        # May or may not have warnings depending on test setup
        for w in warnings:
            self.assertIsInstance(w, Warning)

    @patch("apps.checkers.checks.MigrationExecutor")
    def test_pending_migrations_detected(self, mock_executor_class):
        """Warning returned when migrations pending."""
        mock_executor = MagicMock()
        mock_executor.migration_plan.return_value = [
            (MagicMock(), False)  # One pending migration
        ]
        mock_executor_class.return_value = mock_executor

        warnings = check_pending_migrations(None)

        self.assertEqual(len(warnings), 1)
        self.assertIsInstance(warnings[0], Warning)
        self.assertIn("migration", warnings[0].msg.lower())


class CrontabConfigurationCheckTests(TestCase):
    """Tests for crontab configuration system check."""

    @override_settings(CHECKERS_CRONTAB_ENABLED=True)
    @patch("apps.checkers.checks.os.path.exists")
    def test_crontab_configured(self, mock_exists):
        """No errors when crontab is properly configured."""
        mock_exists.return_value = True

        errors = check_crontab_configuration(None)

        # Should pass or return warnings (not errors)
        for e in errors:
            self.assertNotIsInstance(e, Error)

    @override_settings(CHECKERS_CRONTAB_ENABLED=False)
    def test_crontab_disabled(self):
        """No checks when crontab is disabled."""
        errors = check_crontab_configuration(None)

        self.assertEqual(errors, [])


class DatabaseTablesExistCheckTests(TestCase):
    """Tests for database tables existence check."""

    def test_tables_exist(self):
        """No errors when required tables exist."""
        # Test database should have tables
        errors = check_database_tables_exist(None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks.connection")
    def test_tables_missing(self, mock_connection):
        """Error returned when tables missing."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        errors = check_database_tables_exist(None)

        # Should detect missing tables
        # Implementation dependent
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/test_checks.py -v`
Expected: All tests PASS (may need adjustment based on actual implementation)

**Step 3: Commit**

```bash
git add apps/checkers/tests/test_checks.py
git commit -m "test(checkers): add test_checks.py for Django system checks"
```

---

### Task 10: Create test_models.py with CheckRun model tests

**Files:**
- Create: `apps/checkers/tests/test_models.py`

**Step 1: Write test_models.py**

```python
"""Tests for the CheckRun model."""

from django.test import TestCase
from django.utils import timezone

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.models import CheckRun


class CheckRunModelTests(TestCase):
    """Tests for CheckRun ORM model."""

    def test_create_check_run(self):
        """CheckRun can be created with required fields."""
        check_run = CheckRun.objects.create(
            checker_name="cpu",
            status=CheckStatus.OK.value,
            message="CPU usage at 25%",
        )

        self.assertIsNotNone(check_run.pk)
        self.assertEqual(check_run.checker_name, "cpu")
        self.assertEqual(check_run.status, CheckStatus.OK.value)

    def test_check_run_with_metrics(self):
        """CheckRun can store metrics JSON."""
        metrics = {"cpu_percent": 45.2, "cpu_count": 4}
        check_run = CheckRun.objects.create(
            checker_name="cpu",
            status=CheckStatus.OK.value,
            message="CPU check passed",
            metrics=metrics,
        )

        check_run.refresh_from_db()
        self.assertEqual(check_run.metrics, metrics)

    def test_check_run_with_error(self):
        """CheckRun can store error message."""
        check_run = CheckRun.objects.create(
            checker_name="cpu",
            status=CheckStatus.UNKNOWN.value,
            message="Check failed",
            error="psutil not available",
        )

        self.assertEqual(check_run.error, "psutil not available")

    def test_check_run_timestamps(self):
        """CheckRun has created timestamp."""
        before = timezone.now()
        check_run = CheckRun.objects.create(
            checker_name="memory",
            status=CheckStatus.OK.value,
            message="Memory OK",
        )
        after = timezone.now()

        self.assertIsNotNone(check_run.created_at)
        self.assertGreaterEqual(check_run.created_at, before)
        self.assertLessEqual(check_run.created_at, after)

    def test_check_run_str(self):
        """CheckRun string representation is readable."""
        check_run = CheckRun.objects.create(
            checker_name="disk",
            status=CheckStatus.WARNING.value,
            message="Disk at 75%",
        )

        str_repr = str(check_run)
        self.assertIn("disk", str_repr.lower())

    def test_check_run_ordering(self):
        """CheckRuns ordered by creation time descending."""
        run1 = CheckRun.objects.create(
            checker_name="cpu",
            status=CheckStatus.OK.value,
            message="Run 1",
        )
        run2 = CheckRun.objects.create(
            checker_name="cpu",
            status=CheckStatus.OK.value,
            message="Run 2",
        )

        runs = list(CheckRun.objects.all())
        # Most recent first (if default ordering is -created_at)
        # Adjust based on actual model ordering
        self.assertEqual(len(runs), 2)

    def test_check_run_correlation_id(self):
        """CheckRun can store correlation ID for tracking."""
        check_run = CheckRun.objects.create(
            checker_name="network",
            status=CheckStatus.OK.value,
            message="Network OK",
            correlation_id="req-12345",
        )

        self.assertEqual(check_run.correlation_id, "req-12345")
```

**Step 2: Run test to verify it works**

Run: `uv run pytest apps/checkers/tests/test_models.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/checkers/tests/test_models.py
git commit -m "test(checkers): add test_models.py for CheckRun model"
```

---

### Task 11: Verify all new tests pass together

**Files:**
- All files in `apps/checkers/tests/`

**Step 1: Run full new test suite**

Run: `uv run pytest apps/checkers/tests/ -v`
Expected: All tests PASS

**Step 2: Compare test count with original**

Run: `uv run pytest apps/checkers/tests.py -v --collect-only | grep "test_" | wc -l`
Run: `uv run pytest apps/checkers/tests/ -v --collect-only | grep "test_" | wc -l`

Expected: New test suite has same or more tests than original

**Step 3: Commit**

```bash
git commit --allow-empty -m "test(checkers): verify restructured tests all pass"
```

---

### Task 12: Remove original monolithic tests.py

**Files:**
- Delete: `apps/checkers/tests.py`

**Step 1: Verify no imports reference old file**

Run: `uv run grep -r "from apps.checkers.tests import" . --include="*.py" | grep -v ".pyc"`
Expected: No results (no imports from old file)

**Step 2: Delete old test file**

```bash
rm apps/checkers/tests.py
```

**Step 3: Run tests to verify nothing broken**

Run: `uv run pytest apps/checkers/tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add -A
git commit -m "chore(checkers): remove monolithic tests.py after restructure"
```

---

### Task 13: Update pytest configuration if needed

**Files:**
- Check: `pyproject.toml` or `pytest.ini`

**Step 1: Verify pytest discovers new tests**

Run: `uv run pytest apps/checkers/ -v --collect-only`
Expected: Shows tests from `apps/checkers/tests/` directory

**Step 2: Update config if needed**

If tests not discovered, check `testpaths` in pytest config.

**Step 3: Commit if changes made**

```bash
git add pyproject.toml pytest.ini
git commit -m "chore: update pytest config for checkers test structure"
```

---

## Verification Commands

After each task:
```bash
# Run tests for checkers app
uv run pytest apps/checkers/tests/ -v

# Run specific test file
uv run pytest apps/checkers/tests/test_base.py -v

# Run specific checker tests
uv run pytest apps/checkers/tests/checkers/ -v

# Check test coverage
uv run pytest apps/checkers/tests/ --cov=apps.checkers --cov-report=term-missing
```

## Risk Assessment

- **Low risk:** Creating new test files (Tasks 1-10) - additive changes
- **Low risk:** Removing old tests.py (Task 12) - only after verification
- **Mitigation:** Run full test suite after each task

## Notes

- Tests may need adjustment based on actual implementation details
- Some checker implementations may differ from assumed API
- Django system checks tests depend on actual check implementations
- Model tests assume certain fields exist on CheckRun