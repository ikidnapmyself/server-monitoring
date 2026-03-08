"""Tests for apps.orchestration.formatters."""

from django.test import SimpleTestCase

from apps.orchestration.formatters import (
    build_notification_body,
    format_check_summary,
    format_ingest_summary,
    format_intelligence_summary,
)


class TestFormatIngestSummary(SimpleTestCase):
    def test_dict_input(self):
        data = {
            "incident_id": 42,
            "severity": "critical",
            "source": "grafana",
            "alerts_created": 2,
            "alerts_updated": 1,
            "alerts_resolved": 0,
            "incidents_created": 1,
            "incidents_updated": 0,
            "duration_ms": 12.345,
        }
        md = format_ingest_summary(data)
        assert md.startswith("**Ingest Summary**")
        assert "incident_id: `42`" in md
        assert "severity: critical" in md
        assert "alerts_created: 2" in md
        assert "duration_ms: `12.35`" in md

    def test_empty_dict_uses_defaults(self):
        md = format_ingest_summary({})
        assert "alerts_created: 0" in md
        assert "duration_ms: `0.0`" in md

    def test_non_dict_input(self):
        md = format_ingest_summary("raw string")
        assert "```" in md
        assert "raw string" in md


class TestFormatCheckSummary(SimpleTestCase):
    def test_dict_input(self):
        data = {
            "checks_run": 5,
            "checks_passed": 4,
            "checks_failed": 1,
            "checker_output_ref": "ref-123",
            "duration_ms": 99.9,
        }
        md = format_check_summary(data)
        assert "checks_run: 5" in md
        assert "passed: 4" in md
        assert "failed: 1" in md
        assert "checker_output_ref: `ref-123`" in md

    def test_dict_without_checker_output_ref(self):
        md = format_check_summary({"checks_run": 1})
        assert "checker_output_ref" not in md

    def test_empty_dict_uses_defaults(self):
        md = format_check_summary({})
        assert "checks_run: 0" in md

    def test_non_dict_input(self):
        md = format_check_summary(42)
        assert "```" in md
        assert "42" in md


class TestFormatIntelligenceSummary(SimpleTestCase):
    def test_dict_with_summary_and_cause(self):
        data = {
            "summary": "High CPU usage",
            "probable_cause": "Runaway process",
            "recommendations": [{"title": "Kill it"}],
        }
        md = format_intelligence_summary(data)
        assert "summary: High CPU usage" in md
        assert "probable_cause: Runaway process" in md
        assert "recommendations: 1" in md

    def test_dict_without_optional_fields(self):
        md = format_intelligence_summary({})
        assert "recommendations: 0" in md
        assert "summary" not in md.split("\n", 1)[1]

    def test_top_processes_included(self):
        data = {
            "recommendations": [
                {
                    "title": "CPU",
                    "details": {
                        "top_processes": [
                            {"pid": 1234, "name": "python", "cpu_percent": 95.5},
                            {"pid": 5678, "cmdline": "node", "cpu_percent": None},
                        ]
                    },
                }
            ]
        }
        md = format_intelligence_summary(data)
        assert "`1234` python" in md
        assert "95.5%" in md
        assert "`5678` node" in md

    def test_top_processes_cpu_non_numeric(self):
        data = {
            "recommendations": [
                {
                    "title": "X",
                    "details": {
                        "top_processes": [
                            {"pid": 1, "name": "a", "cpu_percent": "bad"},
                        ]
                    },
                }
            ]
        }
        md = format_intelligence_summary(data)
        assert "bad" in md

    def test_non_dict_input(self):
        md = format_intelligence_summary(["list", "input"])
        assert "```" in md

    def test_empty_top_processes_list(self):
        data = {"recommendations": [{"title": "X", "details": {"top_processes": []}}]}
        md = format_intelligence_summary(data)
        assert "top_processes" not in md

    def test_no_details_in_recommendation(self):
        data = {"recommendations": [{"title": "X"}]}
        md = format_intelligence_summary(data)
        assert "top_processes" not in md


class TestBuildNotificationBody(SimpleTestCase):
    def test_all_sections(self):
        body = build_notification_body("msg", "ingest", "check", "intel")
        assert "msg" in body
        assert "---" in body
        parts = body.split("\n\n---\n\n")
        assert len(parts) == 4

    def test_empty_sections_skipped(self):
        body = build_notification_body("msg", "", "", "intel")
        parts = body.split("\n\n---\n\n")
        assert len(parts) == 2
        assert parts[0] == "msg"
        assert parts[1] == "intel"

    def test_all_empty(self):
        assert build_notification_body("", "", "", "") == ""

    def test_single_section(self):
        body = build_notification_body("only this", "", "", "")
        assert body == "only this"
