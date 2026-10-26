"""Tests for config/dashboard.py: get_dashboard_context."""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from config.dashboard import get_dashboard_context


def _root():
    return get_user_model().objects.get_or_create(
        username="root", defaults={"is_superuser": True, "is_staff": True}
    )[0]


@pytest.mark.django_db
class TestGetDashboardContext(TestCase):
    def test_returns_expected_keys(self):
        ctx = get_dashboard_context(_root())
        expected_keys = {
            "active_incidents",
            "pipeline_health",
            "recent_check_runs",
            "failed_pipelines",
            "top_failing_checkers",
            "failure_reasons",
            "provider_usage",
            "readiness",
            "fleet_metrics",
        }
        assert set(ctx.keys()) == expected_keys

    def test_empty_database_says_there_were_no_runs(self):
        ctx = get_dashboard_context(_root())
        assert ctx["pipeline_health"] == {
            "headline": "No runs in the last 24 hours.",
            "rows": [],
        }
        assert ctx["active_incidents"]["total"] == 0

    def test_with_data(self):
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
        from apps.orchestration.models import PipelineRun, PipelineStatus

        Incident.objects.create(severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN)
        PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.NOTIFIED,
            created_at=timezone.now(),
        )

        ctx = get_dashboard_context(_root())
        assert ctx["active_incidents"]["total"] == 1
        assert ctx["active_incidents"]["critical"] == 1
        health = ctx["pipeline_health"]
        assert health["headline"] == "100% of runs finished"
        assert health["rows"][0] == {
            "label": "Finished",
            "count": "1",
            "url": "/console/runs/?status=finished&since=24h",
        }


@pytest.mark.django_db
class TestTrendRowsAreReachable:
    """Every trend row names something filterable, so every row is a link.

    A count with nowhere to click is the dead-end these cover: the operator can
    see that ``disk`` failed twelve times and has to rebuild the filter by hand.
    """

    def test_a_failing_checker_links_its_console_check_runs(self, admin_client):
        from apps.checkers.models import CheckRun, CheckStatus

        CheckRun.objects.create(
            checker_name="disk", hostname="h", status=CheckStatus.CRITICAL, message="m"
        )
        row = get_dashboard_context(_root())["top_failing_checkers"][0]
        expected = "/console/checks/?status=failing&checker=disk&since=7d"
        assert row == {"label": "Disk space", "count": "1 failing result", "url": expected}
        assert admin_client.get(expected).status_code == 200

    def test_a_failure_reason_links_the_console_runs_it_names(self, admin_client):
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t",
            run_id="r",
            status=PipelineStatus.FAILED,
            last_error_message="no_route: nothing matched",
        )
        row = get_dashboard_context(_root())["failure_reasons"][0]
        expected = "/console/runs/?status=failed&reason=no_route&since=7d"
        assert row["label"] == "No route matches this incident"
        assert row["url"] == expected
        assert admin_client.get(expected).status_code == 200

    def test_a_provider_links_its_console_analyses(self, admin_client):
        from apps.intelligence.models import AnalysisRun

        AnalysisRun.objects.create(trace_id="t", provider="openai", status="succeeded")
        row = get_dashboard_context(_root())["provider_usage"][0]
        expected = "/console/analyses/?provider=openai&since=7d"
        assert row == {"label": "OpenAI", "count": "1 analysis, 0 tokens", "url": expected}
        assert admin_client.get(expected).status_code == 200

    def test_a_recent_check_run_links_its_console_page(self, admin_client):
        from django.urls import reverse

        from apps.checkers.models import CheckRun, CheckStatus

        run = CheckRun.objects.create(
            checker_name="cpu", hostname="h", status=CheckStatus.OK, message="m"
        )
        body = admin_client.get(reverse("admin:index")).content.decode()
        assert f'<a href="/console/checks/{run.pk}/">cpu</a>' in body
        assert '<a href="/console/checks/">View all &rarr;</a>' in body
        assert reverse("admin:checkers_checkrun_change", args=[run.pk]) not in body


@pytest.mark.django_db
def test_a_reader_without_the_permission_gets_text_not_a_dead_link(client):
    from django.urls import reverse

    from apps.checkers.models import CheckRun
    from apps.intelligence.models import AnalysisRun
    from apps.orchestration.models import PipelineRun

    PipelineRun.objects.create(
        trace_id="t", run_id="r", status="failed", last_error_message="no_route: x"
    )
    run = CheckRun.objects.create(checker_name="disk", hostname="h", status="critical", message="m")
    AnalysisRun.objects.create(trace_id="t", provider="openai")
    staff = get_user_model().objects.create_user("plain", password="x", is_staff=True)

    ctx = get_dashboard_context(staff)

    rows = [
        *ctx["pipeline_health"]["rows"],
        *ctx["top_failing_checkers"],
        *ctx["failure_reasons"],
        *ctx["provider_usage"],
    ]
    assert len(rows) == 6
    assert all(row["url"] == "" for row in rows)
    client.force_login(staff)
    body = client.get(reverse("admin:index")).content.decode()
    assert "<td>No route matches this incident</td>" in body
    assert "<td>OpenAI</td>" in body
    assert "<span>Failed: <strong>1</strong></span>" in body
    assert f"/console/checks/{run.pk}/" not in body
    assert "/console/checks/" not in body
    assert "/console/runs/?status=failed&amp;reason=" not in body
    assert "/console/analyses/" not in body
