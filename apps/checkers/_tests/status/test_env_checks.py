"""Tests for env file consistency checks."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from apps.checkers.status.env_checks import (
    _read_file,
    parse_env_keys,
    parse_sample_keys,
    parse_settings_env_refs,
    run,
)


class ParseEnvKeysTests(TestCase):
    def test_parses_simple_keys(self):
        content = "FOO=bar\nBAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO", "BAZ"})

    def test_ignores_comments(self):
        content = "# comment\nFOO=bar\n# BAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO"})

    def test_ignores_blank_lines(self):
        content = "\nFOO=bar\n\n\nBAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO", "BAZ"})

    def test_handles_values_with_equals(self):
        content = "URL=https://example.com?a=1&b=2\n"
        self.assertEqual(parse_env_keys(content), {"URL"})

    def test_empty_value(self):
        content = "FOO=\n"
        self.assertEqual(parse_env_keys(content), {"FOO"})

    def test_empty_content(self):
        self.assertEqual(parse_env_keys(""), set())

    def test_line_without_equals(self):
        content = "FOO=bar\nno_equals_here\nBAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO", "BAZ"})

    def test_empty_key_before_equals(self):
        content = "=value\nFOO=bar\n"
        self.assertEqual(parse_env_keys(content), {"FOO"})


class ParseSampleKeysTests(TestCase):
    def test_parses_active_and_commented_keys(self):
        content = "FOO=bar\n# BAZ=qux\n# pure comment no equals\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, {"BAZ"})

    def test_double_hash_ignored(self):
        content = "## heading\nFOO=bar\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, set())

    def test_commented_with_space(self):
        content = "# OPTIONAL_KEY=default_value\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, set())
        self.assertEqual(commented, {"OPTIONAL_KEY"})

    def test_blank_lines_in_sample(self):
        content = "\nFOO=bar\n\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, set())

    def test_empty_key_in_sample(self):
        content = "=value\nFOO=bar\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})

    def test_commented_lowercase_key_ignored(self):
        content = "# some description with equals=sign\n# VALID_KEY=val\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(commented, {"VALID_KEY"})


class ReadFileTests(TestCase):
    def test_reads_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("FOO=bar\n")
            f.flush()
            result = _read_file(Path(f.name))
        self.assertEqual(result, "FOO=bar\n")

    def test_returns_none_for_missing_file(self):
        result = _read_file(Path("/nonexistent/path/.env"))
        self.assertIsNone(result)


class ParseSettingsEnvRefsTests(TestCase):
    def test_parses_environ_get(self):
        content = 'FOO = os.environ.get("MY_VAR", "default")\n'
        self.assertEqual(parse_settings_env_refs(content), {"MY_VAR"})

    def test_parses_single_quotes(self):
        content = "FOO = os.environ.get('MY_VAR')\n"
        self.assertEqual(parse_settings_env_refs(content), {"MY_VAR"})

    def test_multiple_refs(self):
        content = 'A = os.environ.get("VAR_A", "")\n' 'B = os.environ.get("VAR_B", "0")\n'
        self.assertEqual(parse_settings_env_refs(content), {"VAR_A", "VAR_B"})

    def test_no_refs(self):
        self.assertEqual(parse_settings_env_refs("x = 1\n"), set())


class RunEnvChecksTests(TestCase):
    @patch("apps.checkers.status.env_checks._read_file")
    def test_missing_env_file(self, mock_read):
        mock_read.return_value = None
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any(".env file not found" in r.message for r in errors))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_missing_sample_file(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any(".env.sample not found" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_sample_key_missing_from_env(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\nMISSING_KEY=default\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("MISSING_KEY" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_env_key_not_in_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\nEXTRA=val\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("EXTRA" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_settings_ref_missing_from_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("UNDOCUMENTED_VAR", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("UNDOCUMENTED_VAR" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_sample_key_unreferenced_in_settings(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\nSTALE=val\n"
            if path.name == ".env.sample":
                return "FOO=bar\nSTALE=old\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("FOO", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("STALE" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_all_consistent_returns_ok(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=default\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("FOO", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)
