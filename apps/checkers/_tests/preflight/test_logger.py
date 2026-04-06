"""Tests for preflight JSON-line logger."""

import json
import tempfile
from pathlib import Path

from django.test import TestCase

from apps.checkers.preflight import CheckResult
from apps.checkers.preflight.logger import log_results


class LogResultsTests(TestCase):
    def test_appends_json_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "checks.log"
            checks = [
                CheckResult(level="ok", message="test passed"),
                CheckResult(level="warn", message="something wrong", hint="fix it"),
            ]
            log_results(checks, log_path)

            content = log_path.read_text().strip()
            data = json.loads(content)
            self.assertIn("timestamp", data)
            self.assertEqual(data["passed"], 1)
            self.assertEqual(data["warnings"], 1)
            self.assertEqual(data["errors"], 0)
            self.assertEqual(data["info"], 0)
            self.assertEqual(len(data["checks"]), 2)

    def test_appends_multiple_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "checks.log"
            checks = [CheckResult(level="ok", message="ok")]
            log_results(checks, log_path)
            log_results(checks, log_path)

            lines = log_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "subdir" / "checks.log"
            log_results([], log_path)
            self.assertTrue(log_path.exists())

    def test_info_field_counted_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "checks.log"
            checks = [
                CheckResult(level="ok", message="ok"),
                CheckResult(level="info", message="info msg"),
                CheckResult(level="warn", message="warn msg"),
                CheckResult(level="error", message="error msg"),
            ]
            log_results(checks, log_path)
            data = json.loads(log_path.read_text().strip())
            self.assertEqual(data["passed"], 1)
            self.assertEqual(data["info"], 1)
            self.assertEqual(data["warnings"], 1)
            self.assertEqual(data["errors"], 1)

    def test_handles_write_error_gracefully(self):
        checks = [CheckResult(level="ok", message="ok")]
        log_results(checks, Path("/proc/fake/checks.log"))
