"""Tests for CheckResult dataclass."""

from django.test import TestCase

from apps.checkers.preflight import CheckResult


class CheckResultTests(TestCase):
    def test_defaults(self):
        r = CheckResult(level="warn", message="something")
        self.assertEqual(r.level, "warn")
        self.assertEqual(r.message, "something")
        self.assertEqual(r.hint, "")

    def test_all_fields(self):
        r = CheckResult(level="error", message="bad", hint="fix it")
        self.assertEqual(r.hint, "fix it")

    def test_valid_levels(self):
        for level in ("ok", "info", "warn", "error"):
            r = CheckResult(level=level, message="test")
            self.assertEqual(r.level, level)
