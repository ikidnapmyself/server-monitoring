"""Unit tests for the shared metrics formatter."""

from io import StringIO

from django.test import SimpleTestCase

from apps.checkers.management.commands._metrics_format import write_metrics


class WriteMetricsTests(SimpleTestCase):
    """Direct unit tests for write_metrics."""

    INDENT = "       "  # 7 spaces, matching check_health's indent

    def _render(self, metrics, indent=None):
        out = StringIO()
        write_metrics(out, metrics, indent=indent if indent is not None else self.INDENT)
        return out.getvalue()

    def test_no_disk_sections(self):
        output = self._render({"cpu_percent": 12.5})
        self.assertNotIn("Space Hogs", output)
        self.assertNotIn("Old Files", output)
        self.assertNotIn("Large Files", output)
        self.assertIn("cpu percent: 12.5", output)

    def test_section_all_shown_when_under_cap(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 10.0} for i in range(5)]
        output = self._render({"space_hogs": items})
        self.assertIn("Space Hogs: 50.0 MB (5 items, all shown)", output)
        self.assertNotIn("... and", output)

    def test_section_truncated_with_trailer(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        output = self._render({"space_hogs": items})
        self.assertIn("Space Hogs: 1206.0 MB (12 items, top 10 shown)", output)
        self.assertIn("/tmp/file0", output)
        self.assertIn("100.5 MB", output)
        self.assertIn("30d old", output)
        self.assertIn("... and 2 more  (201.0 MB)", output)

    def test_largest_section_shown_in_full(self):
        space_hogs = [{"path": f"/tmp/s{i}", "size_mb": 5.0} for i in range(12)]
        old_files = [{"path": f"/tmp/o{i}", "size_mb": 50.0, "age_days": 7} for i in range(12)]
        output = self._render({"space_hogs": space_hogs, "old_files": old_files})
        self.assertIn("Space Hogs: 60.0 MB (12 items, top 10 shown)", output)
        self.assertIn("... and 2 more  (10.0 MB)", output)
        self.assertIn("Old Files: 600.0 MB (12 items, all shown)", output)
        self.assertIn("/tmp/o11", output)

    def test_three_sections_largest_wins(self):
        space_hogs = [{"path": f"/v/s{i}", "size_mb": 1.0} for i in range(11)]
        old_files = [{"path": f"/v/o{i}", "size_mb": 2.0, "age_days": 5} for i in range(11)]
        large_files = [{"path": f"/h/l{i}", "size_mb": 100.0} for i in range(11)]
        output = self._render(
            {
                "space_hogs": space_hogs,
                "old_files": old_files,
                "large_files": large_files,
            }
        )
        self.assertIn("Space Hogs: 11.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Old Files: 22.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Large Files: 1100.0 MB (11 items, all shown)", output)
        self.assertIn("/h/l10", output)
        self.assertNotIn("/v/s10", output)
        self.assertNotIn("/v/o10", output)

    def test_old_files_section_with_age_annotation(self):
        items = [{"path": "/tmp/old", "size_mb": 50.0, "age_days": 30}]
        output = self._render({"old_files": items})
        self.assertIn("Old Files: 50.0 MB (1 items, all shown)", output)
        self.assertIn("/tmp/old", output)
        self.assertIn("50.0 MB", output)
        self.assertIn("(30d old)", output)

    def test_large_files_section(self):
        items = [{"path": "/tmp/large", "size_mb": 200.0}]
        output = self._render({"large_files": items})
        self.assertIn("Large Files: 200.0 MB (1 items, all shown)", output)
        self.assertNotIn("d old", output)

    def test_total_recoverable(self):
        output = self._render({"total_recoverable_mb": 500.0})
        self.assertIn("Total recoverable: 500.0 MB", output)

    def test_recommendations(self):
        output = self._render({"recommendations": [["clean /tmp"]]})
        self.assertIn("Recommendations:", output)
        self.assertIn("- clean /tmp", output)

    def test_recommendation_with_multiline_renders_indented(self):
        output = self._render({"recommendations": [["Title:", "step one", "step two"]]})
        self.assertIn("Recommendations:", output)
        self.assertIn("- Title:", output)
        self.assertIn("    step one", output)
        self.assertIn("    step two", output)

    def test_empty_recommendation_skipped(self):
        output = self._render({"recommendations": [[], ["Real title"]]})
        self.assertIn("- Real title", output)
        # Should not produce stray "- " bullets from the empty entry
        self.assertNotIn("- \n", output)

    def test_nested_dict(self):
        output = self._render(
            {
                "paths": {
                    "/": {"total": 100, "used": 50},
                    "free_pct": 50.0,
                    "label": "root",
                }
            }
        )
        self.assertIn("paths:", output)
        self.assertIn("/: total: 100, used: 50", output)
        self.assertIn("free_pct: 50.0", output)
        self.assertIn("label: root", output)

    def test_flat_key_underscore_to_space_and_float_format(self):
        output = self._render({"cpu_percent": 95.5})
        self.assertIn("cpu percent: 95.5", output)

    def test_flat_key_integer_value(self):
        output = self._render({"count": 42})
        self.assertIn("count: 42", output)

    def test_indent_parameter(self):
        items = [{"path": "/tmp/file0", "size_mb": 50.0}]
        output = self._render({"space_hogs": items}, indent="    ")
        self.assertIn("    Space Hogs: 50.0 MB (1 items, all shown)", output)
        self.assertIn("      - /tmp/file0  50.0 MB", output)
        self.assertNotIn("       Space Hogs", output)

    def test_platform_key_is_skipped(self):
        output = self._render({"platform": "darwin", "cpu_percent": 12.5})
        self.assertNotIn("platform", output)
        self.assertIn("cpu percent: 12.5", output)

    def test_empty_metrics(self):
        output = self._render({})
        self.assertEqual(output, "")
