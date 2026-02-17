# CPU Multi-Sample Measurement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the CPU checker's single-snapshot measurement with multi-sample averaging (avg/min/max) for meaningful baseline comparison.

**Architecture:** Modify `CPUChecker.check()` to loop N times calling `psutil.cpu_percent()`, collecting samples into a list, then computing avg/min/max. Update the management command CLI flags and README. No new files — this is a pure refactor of existing code.

**Tech Stack:** Python, psutil, Django management commands, pytest

**Design doc:** `docs/plans/2026-02-17-cpu-multi-sample-design.md`

---

## Task 1: Rewrite CPUChecker with multi-sample logic

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_cpu.py`
- Modify: `apps/checkers/checkers/cpu.py`

**Step 1: Write failing tests**

Replace the entire content of `apps/checkers/_tests/checkers/test_cpu.py` with:

```python
"""Tests for the CPU checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, CPUChecker


class CPUCheckerInitTests(TestCase):
    """Tests for CPUChecker initialization."""

    def test_default_samples(self):
        checker = CPUChecker()
        self.assertEqual(checker.samples, 5)

    def test_default_sample_interval(self):
        checker = CPUChecker()
        self.assertEqual(checker.sample_interval, 1.0)

    def test_custom_samples(self):
        checker = CPUChecker(samples=10, sample_interval=0.5)
        self.assertEqual(checker.samples, 10)
        self.assertEqual(checker.sample_interval, 0.5)


class CPUCheckerTests(TestCase):
    """Tests for CPUChecker.check() multi-sample behavior."""

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_averages_multiple_samples(self, mock_psutil):
        """Average of [20, 40, 60] = 40 -> OK."""
        mock_psutil.cpu_percent.side_effect = [20.0, 40.0, 60.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertAlmostEqual(result.metrics["cpu_percent"], 40.0)
        self.assertAlmostEqual(result.metrics["cpu_min"], 20.0)
        self.assertAlmostEqual(result.metrics["cpu_max"], 60.0)
        self.assertEqual(result.metrics["samples"], 3)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_ok(self, mock_psutil):
        """Average below warning threshold -> OK."""
        mock_psutil.cpu_percent.side_effect = [30.0, 40.0, 50.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_warning(self, mock_psutil):
        """Average at 75 -> WARNING (threshold 70)."""
        mock_psutil.cpu_percent.side_effect = [70.0, 75.0, 80.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_critical(self, mock_psutil):
        """Average at 95 -> CRITICAL (threshold 90)."""
        mock_psutil.cpu_percent.side_effect = [90.0, 95.0, 100.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_message_shows_average(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [20.0, 40.0, 60.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertIn("40.0%", result.message)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_metrics_include_cpu_count(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [50.0]
        mock_psutil.cpu_count.return_value = 8

        checker = CPUChecker(samples=1, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.metrics["cpu_count"], 8)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_per_cpu_averages_across_samples(self, mock_psutil):
        """Per-CPU mode: averages each core across samples."""
        # 2 samples, 4 cores each
        mock_psutil.cpu_percent.side_effect = [
            [10.0, 20.0, 80.0, 40.0],  # sample 1
            [30.0, 40.0, 60.0, 20.0],  # sample 2
        ]

        checker = CPUChecker(samples=2, sample_interval=0.0, per_cpu=True)
        result = checker.check()

        # Per-core averages: [20, 30, 70, 30]
        # Max per-core avg = 70 -> WARNING
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertAlmostEqual(result.metrics["cpu_percent"], 70.0)
        self.assertEqual(result.metrics["per_cpu_percent"], [20.0, 30.0, 70.0, 30.0])
        self.assertEqual(result.metrics["cpu_count"], 4)
        self.assertAlmostEqual(result.metrics["cpu_min"], 60.0)
        self.assertAlmostEqual(result.metrics["cpu_max"], 80.0)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_error_returns_unknown(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = RuntimeError("sensor failed")

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_checker_name(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [25.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=1, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.checker_name, "cpu")
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest apps/checkers/_tests/checkers/test_cpu.py -v
```

Expected: Multiple failures — `CPUChecker` does not accept `samples` or `sample_interval` yet.

**Step 3: Implement multi-sample CPUChecker**

Replace the entire content of `apps/checkers/checkers/cpu.py` with:

```python
"""
CPU usage checker with multi-sample averaging.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult


class CPUChecker(BaseChecker):
    """
    Check CPU usage by averaging multiple samples.

    Takes N samples at a fixed interval and computes avg/min/max.
    Status is determined from the average.
    Default: 5 samples, 1 second apart (5 seconds total).
    Default thresholds: warning at 70%, critical at 90%.
    """

    name = "cpu"
    warning_threshold = 70.0
    critical_threshold = 90.0

    def __init__(
        self,
        samples: int = 5,
        sample_interval: float = 1.0,
        per_cpu: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.samples = samples
        self.sample_interval = sample_interval
        self.per_cpu = per_cpu

    def check(self) -> CheckResult:
        try:
            if self.per_cpu:
                return self._check_per_cpu()
            return self._check_system()
        except Exception as e:
            return self._error_result(str(e))

    def _check_system(self) -> CheckResult:
        readings = [
            psutil.cpu_percent(interval=self.sample_interval)
            for _ in range(self.samples)
        ]
        avg = sum(readings) / len(readings)
        metrics = {
            "cpu_percent": round(avg, 1),
            "cpu_min": min(readings),
            "cpu_max": max(readings),
            "samples": self.samples,
            "cpu_count": psutil.cpu_count(),
        }
        status = self._determine_status(avg)
        message = f"CPU usage: {avg:.1f}% (avg of {self.samples} samples)"
        return self._make_result(status=status, message=message, metrics=metrics)

    def _check_per_cpu(self) -> CheckResult:
        all_samples = [
            psutil.cpu_percent(interval=self.sample_interval, percpu=True)
            for _ in range(self.samples)
        ]
        num_cores = len(all_samples[0])
        per_core_avgs = [
            round(sum(s[i] for s in all_samples) / self.samples, 1)
            for i in range(num_cores)
        ]
        # Per-sample max (for min/max across samples)
        per_sample_maxes = [max(s) for s in all_samples]
        cpu_percent = max(per_core_avgs)
        metrics = {
            "cpu_percent": cpu_percent,
            "cpu_min": min(per_sample_maxes),
            "cpu_max": max(per_sample_maxes),
            "samples": self.samples,
            "per_cpu_percent": per_core_avgs,
            "cpu_count": num_cores,
        }
        status = self._determine_status(cpu_percent)
        message = f"CPU usage: {cpu_percent:.1f}% (avg of {self.samples} samples, busiest core)"
        return self._make_result(status=status, message=message, metrics=metrics)
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/checkers/_tests/checkers/test_cpu.py -v
```

Expected: All 10 tests pass.

**Step 5: Commit**

```bash
git add apps/checkers/checkers/cpu.py apps/checkers/_tests/checkers/test_cpu.py
git commit -m "feat: multi-sample CPU measurement with avg/min/max"
```

---

## Task 2: Update run_check management command

**Files:**
- Modify: `apps/checkers/management/commands/run_check.py:61-66,94-96`

**Step 1: Update CLI flags**

In `apps/checkers/management/commands/run_check.py`, replace the `--interval` argument (lines 61-65) with two new arguments:

```python
        parser.add_argument(
            "--samples",
            type=int,
            help="Number of CPU samples to take (cpu checker only).",
        )
        parser.add_argument(
            "--sample-interval",
            type=float,
            help="Seconds between CPU samples (cpu checker only).",
        )
```

Then in the `handle()` method, replace the `interval` kwarg logic (lines 95-96) with:

```python
            if options.get("samples"):
                kwargs["samples"] = options["samples"]
            if options.get("sample_interval"):
                kwargs["sample_interval"] = options["sample_interval"]
```

**Step 2: Run full test suite to verify nothing breaks**

```bash
uv run pytest -v
```

Expected: All tests pass.

**Step 3: Commit**

```bash
git add apps/checkers/management/commands/run_check.py
git commit -m "feat: update run_check CLI with --samples and --sample-interval"
```

---

## Task 3: Update README documentation

**Files:**
- Modify: `apps/checkers/README.md:238-244`

**Step 1: Update the CPU checker options section**

Replace lines 238-244 in `apps/checkers/README.md`:

```markdown
#### CPU checker options

- `--samples` (integer; default 5) — number of samples to take
- `--sample-interval` (seconds; default 1.0) — seconds between samples
- `--per-cpu` (use the busiest core for the status)

```bash
uv run python manage.py run_check cpu --samples 10 --sample-interval 0.5 --per-cpu
```
```

**Step 2: Commit**

```bash
git add apps/checkers/README.md
git commit -m "docs: update CPU checker options in README"
```

---

## Task 4: Full verification

**Step 1: Run all tests**

```bash
uv run pytest -v
```

Expected: All tests pass.

**Step 2: Run Django system checks**

```bash
uv run python manage.py check
```

Expected: System check identified no issues.

**Step 3: Run linters**

```bash
uv run black --check .
uv run ruff check .
```

Expected: No issues.

**Step 4: Manual smoke test**

```bash
uv run python manage.py run_check cpu --samples 3 --sample-interval 0.5
uv run python manage.py run_check cpu --samples 3 --sample-interval 0.5 --per-cpu --json
```

Expected: Output shows `cpu_percent` (avg), `cpu_min`, `cpu_max`, `samples: 3`.

---

## Files Summary

| File | Change |
|------|--------|
| `apps/checkers/checkers/cpu.py` | Replace `interval` with `samples` + `sample_interval`, add sampling loop with avg/min/max |
| `apps/checkers/_tests/checkers/test_cpu.py` | Rewrite all tests for multi-sample behavior (10 tests) |
| `apps/checkers/management/commands/run_check.py` | Replace `--interval` with `--samples` and `--sample-interval` |
| `apps/checkers/README.md` | Update CPU checker options documentation |
