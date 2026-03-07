"""Tests for config/dashboard.py — prettify_json and get_dashboard_context."""

import pytest
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from config.dashboard import get_dashboard_context, prettify_json


class TestPrettifyJson(SimpleTestCase):
    def test_renders_dict_as_pre_block(self):
        result = prettify_json({"key": "value"})
        assert "<pre" in result
        assert "key" in result

    def test_renders_empty_dict(self):
        result = prettify_json({})
        assert "{}" in result

    def test_none_returns_dash(self):
        assert prettify_json(None) == "-"

    def test_renders_nested_structure(self):
        result = prettify_json({"a": {"b": [1, 2]}})
        assert "<pre" in result


@pytest.mark.django_db
class TestGetDashboardContext(TestCase):
    def test_returns_expected_keys(self):
        ctx = get_dashboard_context()
        expected_keys = {
            "active_incidents",
            "pipeline_health",
            "recent_check_runs",
            "failed_pipelines",
            "top_failing_checkers",
            "top_error_types",
            "provider_usage",
        }
        assert set(ctx.keys()) == expected_keys

    def test_pipeline_health_structure(self):
        ctx = get_dashboard_context()
        health = ctx["pipeline_health"]
        assert "total" in health
        assert "successful" in health
        assert "failed" in health
        assert "success_rate" in health

    def test_empty_database_returns_zeros(self):
        ctx = get_dashboard_context()
        assert ctx["pipeline_health"]["total"] == 0
        assert ctx["pipeline_health"]["success_rate"] == 0
        assert ctx["active_incidents"]["total"] == 0

    def test_with_data(self):
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
        from apps.orchestration.models import PipelineRun, PipelineStatus

        Incident.objects.create(
            title="Test", severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN
        )
        PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.NOTIFIED,
            created_at=timezone.now(),
        )

        ctx = get_dashboard_context()
        assert ctx["active_incidents"]["total"] == 1
        assert ctx["active_incidents"]["critical"] == 1
        assert ctx["pipeline_health"]["total"] == 1
        assert ctx["pipeline_health"]["success_rate"] == 100.0
