"""Tests for base checker classes and data structures."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers import BaseChecker, CheckResult, CheckStatus
from apps.checkers.models import CheckRun


class FakeChecker(BaseChecker):
    """Concrete checker for testing."""

    name = "fake"

    def __init__(self, result=None, error=None, **kwargs):
        super().__init__(**kwargs)
        self._result = result or CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"value": 42},
            checker_name="fake",
        )
        self._error = error

    def check(self):
        if self._error:
            raise self._error
        return self._result


class CheckResultTests(TestCase):
    """Tests for the CheckResult dataclass."""

    def test_is_ok_returns_true_for_ok_status(self):
        result = CheckResult(status=CheckStatus.OK, message="All good")
        self.assertTrue(result.is_ok())

    def test_is_ok_returns_false_for_warning_status(self):
        result = CheckResult(status=CheckStatus.WARNING, message="Warning")
        self.assertFalse(result.is_ok())

    def test_is_critical_returns_true_for_critical_status(self):
        result = CheckResult(status=CheckStatus.CRITICAL, message="Critical")
        self.assertTrue(result.is_critical())

    def test_result_has_all_fields(self):
        result = CheckResult(
            status=CheckStatus.OK,
            message="Test message",
            metrics={"value": 42},
            checker_name="test",
            error=None,
        )
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.message, "Test message")
        self.assertEqual(result.metrics, {"value": 42})
        self.assertEqual(result.checker_name, "test")
        self.assertIsNone(result.error)


class BaseCheckerTests(TestCase):
    """Tests for the BaseChecker ABC."""

    def test_determine_status_ok(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(50), CheckStatus.OK)

    def test_determine_status_warning(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(75), CheckStatus.WARNING)

    def test_determine_status_critical(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(95), CheckStatus.CRITICAL)

    def test_custom_thresholds(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=50, critical_threshold=80)
        self.assertEqual(checker.warning_threshold, 50)
        self.assertEqual(checker.critical_threshold, 80)


class BaseCheckerRunTests(TestCase):
    """Tests for BaseChecker.run() audit logging."""

    def test_run_returns_check_result(self):
        expected = CheckResult(
            status=CheckStatus.WARNING,
            message="High usage",
            metrics={"cpu": 85},
            checker_name="fake",
        )
        checker = FakeChecker(result=expected)
        result = checker.run()
        self.assertEqual(result, expected)

    def test_run_creates_check_run_record(self):
        checker = FakeChecker(warning_threshold=70, critical_threshold=90)
        checker.run()

        self.assertEqual(CheckRun.objects.count(), 1)
        row = CheckRun.objects.first()
        self.assertEqual(row.checker_name, "fake")
        self.assertEqual(row.status, "ok")
        self.assertEqual(row.message, "All good")
        self.assertEqual(row.metrics, {"value": 42})
        self.assertEqual(row.warning_threshold, 70.0)
        self.assertEqual(row.critical_threshold, 90.0)

    def test_run_stores_error_as_empty_string_when_none(self):
        result = CheckResult(status=CheckStatus.OK, message="fine", checker_name="fake", error=None)
        checker = FakeChecker(result=result)
        checker.run()

        row = CheckRun.objects.first()
        self.assertEqual(row.error, "")

    def test_run_records_duration_ms(self):
        checker = FakeChecker()
        checker.run()

        row = CheckRun.objects.first()
        self.assertGreaterEqual(row.duration_ms, 0)

    def test_run_accepts_trace_id(self):
        checker = FakeChecker()
        checker.run(trace_id="abc")

        row = CheckRun.objects.first()
        self.assertEqual(row.trace_id, "abc")

    def test_run_default_trace_id_empty(self):
        checker = FakeChecker()
        checker.run()

        row = CheckRun.objects.first()
        self.assertEqual(row.trace_id, "")

    def test_run_returns_result_when_db_fails(self):
        checker = FakeChecker()
        with patch("apps.checkers.models.CheckRun.objects") as mock_objects:
            mock_objects.create.side_effect = RuntimeError("DB down")
            result = checker.run()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.message, "All good")

    def test_run_catches_check_exception(self):
        checker = FakeChecker(error=RuntimeError("boom"))
        result = checker.run()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("boom", result.message)
        row = CheckRun.objects.first()
        self.assertEqual(row.status, "unknown")
