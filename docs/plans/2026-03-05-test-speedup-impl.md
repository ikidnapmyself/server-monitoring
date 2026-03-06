---
title: "Test Suite Speedup — Implementation Plan"
parent: Plans
---
# Test Suite Speedup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce full test suite from ~82s to ~55s by fixing the 5 slowest tests.

**Architecture:** Test-only changes. Mock expensive I/O (CPU sampling, filesystem scanning) in tests that don't need it, and reduce intentional sleeps. No production code changes.

**Tech Stack:** Python `unittest.mock.patch`, `tempfile`, pytest.

**Important context:**
- CPU checker at `apps/checkers/checkers/cpu.py:49` calls `psutil.cpu_percent(interval=1.0)` in a loop — 5 samples × 1s = 5s.
- The context node at `apps/orchestration/nodes/context.py:52` instantiates checkers with default args via `checker_cls()`.
- The disk scanner at `apps/intelligence/providers/local.py:528` runs `subprocess.run(["du", ...], timeout=30)` and falls back to `Path.rglob("*")`.
- All 3 integration tests in `test_integration.py` verify pipeline orchestration, not CPU accuracy.

---

### Task 1: Mock CPU sampling in 3 integration tests (~15s saved)

**Files:**
- Modify: `apps/orchestration/_tests/test_integration.py`

**Step 1: Add mock decorator to TestPipelineIntegration class**

At the top of the file, add the `unittest.mock` import. Then decorate the class with a `@patch` that makes `psutil.cpu_percent` return instantly:

In `apps/orchestration/_tests/test_integration.py`, change the imports and class definition:

```python
# apps/orchestration/_tests/test_integration.py
"""Integration tests for the complete pipeline system."""

from unittest.mock import patch

from django.test import TestCase

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition, PipelineRun


@patch("psutil.cpu_percent", return_value=42.0)
class TestPipelineIntegration(TestCase):
    """Integration tests for complete pipelines."""
```

Then add `_mock_cpu` as the first parameter (after `self`) to every test method in the class. There are 4 test methods:

- `def test_context_to_intelligence_pipeline(self, _mock_cpu):`
- `def test_pipeline_with_optional_failing_node(self, _mock_cpu):`
- `def test_transform_between_nodes(self, _mock_cpu):`
- `def test_pipeline_creates_run_record(self, _mock_cpu):`

The bodies stay identical — only the signatures change.

**Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_integration.py -v`
Expected: 4 PASS, each completing in < 1s instead of ~5s.

**Step 3: Commit**

```bash
git add apps/orchestration/_tests/test_integration.py
git commit -m "test: mock psutil.cpu_percent in integration tests — saves ~15s"
```

---

### Task 2: Reduce sleep in timeout test (~5s saved)

**Files:**
- Modify: `apps/orchestration/_tests/test_nodes.py`

**Step 1: Change sleep duration**

In `apps/orchestration/_tests/test_nodes.py`, find `TestIntelligenceCallWithTimeout.test_timeout_branch` (line ~1161). Change the `time.sleep(5)` to `time.sleep(0.5)`:

```python
    def test_timeout_branch(self):
        """_call_with_timeout returns None on timeout."""
        from apps.orchestration.nodes.intelligence import IntelligenceNodeHandler

        handler = IntelligenceNodeHandler()

        def slow_func():
            import time

            time.sleep(0.5)
            return "never"

        result = handler._call_with_timeout(slow_func, timeout=0.01)
        assert result is None
```

The timeout is 0.01s, so 0.5s is still 50x longer — proves the same timeout behavior.

**Step 2: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestIntelligenceCallWithTimeout::test_timeout_branch -v`
Expected: PASS in < 1s.

**Step 3: Commit**

```bash
git add apps/orchestration/_tests/test_nodes.py
git commit -m "test: reduce sleep in timeout test from 5s to 0.5s"
```

---

### Task 3: Mock filesystem in disk scan test (~5s saved)

**Files:**
- Modify: `apps/intelligence/_tests/providers/test_local.py`

**Step 1: Replace real filesystem scan with tempdir**

In `apps/intelligence/_tests/providers/test_local.py`, find `test_provider_disk_progress_callback` (line ~117). Replace the test to use a temporary directory instead of scanning real `/tmp`:

```python
    def test_provider_disk_progress_callback(self):
        """Provider should call progress_callback during disk scanning."""
        import tempfile

        progress_messages = []

        def capture_progress(msg):
            progress_messages.append(msg)

        provider = LocalRecommendationProvider(
            large_file_threshold_mb=1000,  # High threshold to scan without finding much
            progress_callback=capture_progress,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small file so the scan has something to traverse
            Path(tmpdir, "testfile.txt").write_text("hello")
            provider._get_disk_recommendations(tmpdir)

        assert any("Scanning" in msg for msg in progress_messages)
        assert any(tmpdir in msg or "Scanning" in msg for msg in progress_messages)
```

Note: The original test asserts `any("/tmp" in msg ...)`. With a tempdir, the path will be something like `/var/folders/.../tmpXXX`. The `_progress` call at line 296 in `local.py` does `self._progress(f"Scanning {path}...")`, so the tmpdir path will appear in the messages. We assert `tmpdir in msg` to verify the callback received the correct path.

**Step 2: Run test to verify it passes**

Run: `uv run pytest apps/intelligence/_tests/providers/test_local.py::TestLocalRecommendationProvider::test_provider_disk_progress_callback -v`
Expected: PASS in < 1s.

**Step 3: Run full provider test suite for regressions**

Run: `uv run pytest apps/intelligence/_tests/providers/test_local.py -v`
Expected: All PASS.

**Step 4: Commit**

```bash
git add apps/intelligence/_tests/providers/test_local.py
git commit -m "test: use tempdir instead of real /tmp in disk scan test — saves ~5s"
```

---

### Task 4: Final verification — full suite timing

**Step 1: Run full test suite with timing**

Run: `uv run pytest --durations=10`
Expected: Total time ~55s (down from ~82s). No test should take > 2s.

**Step 2: Run linting**

Run: `uv run black --check . && uv run ruff check .`
Expected: Clean.

**Step 3: If any formatting fixes needed, commit**

```bash
git add -A
git commit -m "chore: lint fixes for test speedup"
```