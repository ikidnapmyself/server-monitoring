"""Tests for base checker classes and data structures."""

from django.test import TestCase

from apps.checkers.checkers import BaseChecker, CheckResult, CheckStatus


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
