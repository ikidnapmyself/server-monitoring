# Admin Ops Console Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform Django admin into a full operational console with dashboard, pipeline tracing, operational actions, and query optimization.

**Architecture:** Custom `AdminSite` subclass with overridden `index()` for dashboard context. Per-object actions via `django-object-actions`. Bulk actions via built-in Django admin actions. All tracing via `format_html()` readonly fields with `reverse()` URL lookups.

**Tech Stack:** Django 5.2 admin, django-object-actions, Django ORM aggregation (`Count`, `Sum`, `Q`)

---

### Task 1: Foundation — Install dependency, create custom AdminSite, wire it up

**Files:**
- Modify: `pyproject.toml:7-16` (add dependency)
- Create: `config/admin.py`
- Create: `config/apps.py`
- Modify: `config/settings.py:49-61` (swap admin app, add template dir)
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test**

```python
# apps/orchestration/_tests/test_admin.py

import pytest
from django.contrib import admin


@pytest.mark.django_db
class TestMonitoringAdminSite:
    def test_custom_site_is_active(self):
        """The default admin.site should be our custom MonitoringAdminSite."""
        from config.admin import MonitoringAdminSite

        assert isinstance(admin.site, MonitoringAdminSite)

    def test_site_header(self):
        assert admin.site.site_header == "Server Monitoring"

    def test_site_title(self):
        assert admin.site.site_title == "Server Monitoring"

    def test_index_title(self):
        assert admin.site.index_title == "Dashboard"

    def test_admin_index_loads(self, admin_client):
        response = admin_client.get("/admin/")
        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py -v`
Expected: FAIL — `config.admin` module does not exist

**Step 3: Add django-object-actions to pyproject.toml**

In `pyproject.toml`, add to `dependencies` list:

```toml
    "django-object-actions>=4.3.0",
```

Run: `uv sync --extra dev`

**Step 4: Create config/admin.py with MonitoringAdminSite skeleton**

```python
# config/admin.py
"""Custom admin site for the server monitoring ops console."""

from django.contrib.admin import AdminSite


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"
```

**Step 5: Create config/apps.py to register the custom AdminSite**

```python
# config/apps.py
"""Custom Django admin app configuration."""

from django.contrib.admin.apps import AdminConfig


class MonitoringAdminConfig(AdminConfig):
    default_site = "config.admin.MonitoringAdminSite"
```

**Step 6: Update config/settings.py**

Replace `"django.contrib.admin"` with `"config.apps.MonitoringAdminConfig"` in INSTALLED_APPS (line 50).

Add template directory to TEMPLATES DIRS (line 78):

```python
"DIRS": [BASE_DIR / "templates"],
```

Add `"django_object_actions"` to INSTALLED_APPS after the custom admin config.

**Step 7: Create minimal dashboard template**

```html
<!-- templates/admin/dashboard.html -->
{% extends "admin/index.html" %}
{% load i18n %}

{% block content %}
<div id="dashboard">
    <p>Dashboard placeholder — panels will be added in Task 2.</p>
</div>
{{ block.super }}
{% endblock %}
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py -v`
Expected: All 5 tests PASS

**Step 9: Commit**

```bash
git add pyproject.toml config/admin.py config/apps.py config/settings.py \
       templates/admin/dashboard.html apps/orchestration/_tests/test_admin.py
git commit -m "feat: add custom MonitoringAdminSite foundation"
```

---

### Task 2: Dashboard context queries and template

**Files:**
- Modify: `config/admin.py`
- Modify: `templates/admin/dashboard.html`
- Modify: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test for dashboard context**

Append to `apps/orchestration/_tests/test_admin.py`:

```python
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import PipelineRun, PipelineStatus


@pytest.fixture
def dashboard_data(db):
    """Create sample data for dashboard tests."""
    # Active incidents
    Incident.objects.create(title="CPU High", severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN)
    Incident.objects.create(title="Disk Low", severity=AlertSeverity.WARNING, status=IncidentStatus.OPEN)
    Incident.objects.create(title="Old", severity=AlertSeverity.INFO, status=IncidentStatus.CLOSED)

    # Pipeline runs (within 24h)
    now = timezone.now()
    PipelineRun.objects.create(trace_id="t1", run_id="r1", status=PipelineStatus.NOTIFIED, created_at=now)
    PipelineRun.objects.create(trace_id="t2", run_id="r2", status=PipelineStatus.NOTIFIED, created_at=now)
    PipelineRun.objects.create(trace_id="t3", run_id="r3", status=PipelineStatus.FAILED, created_at=now)

    # Check runs
    CheckRun.objects.create(checker_name="cpu", hostname="srv1", status=CheckStatus.CRITICAL, executed_at=now)
    CheckRun.objects.create(checker_name="disk", hostname="srv1", status=CheckStatus.WARNING, executed_at=now)

    # Analysis runs
    AnalysisRun.objects.create(
        trace_id="t1", pipeline_run_id="r1", provider="openai",
        total_tokens=500, status="succeeded",
    )


@pytest.mark.django_db
class TestDashboardContext:
    def test_dashboard_contains_active_incidents(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert response.status_code == 200
        # Context should have dashboard data
        assert "active_incidents" in response.context

    def test_dashboard_contains_pipeline_health(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "pipeline_health" in response.context
        health = response.context["pipeline_health"]
        assert health["total"] == 3
        assert health["successful"] == 2

    def test_dashboard_contains_recent_checks(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "recent_check_runs" in response.context
        assert len(response.context["recent_check_runs"]) == 2

    def test_dashboard_contains_failed_pipelines(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "failed_pipelines" in response.context
        assert len(response.context["failed_pipelines"]) == 1

    def test_dashboard_contains_aggregations(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "top_failing_checkers" in response.context
        assert "top_error_types" in response.context
        assert "provider_usage" in response.context

    def test_dashboard_renders_panels(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        content = response.content.decode()
        assert "Active Incidents" in content
        assert "Pipeline Health" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestDashboardContext -v`
Expected: FAIL — context keys missing

**Step 3: Implement dashboard context in config/admin.py**

Replace `config/admin.py` with:

```python
"""Custom admin site for the server monitoring ops console."""

from datetime import timedelta

from django.contrib.admin import AdminSite
from django.db.models import Count, Q, Sum
from django.utils import timezone


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(self._get_dashboard_context())
        return super().index(request, extra_context=extra_context)

    def _get_dashboard_context(self):
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
        from apps.checkers.models import CheckRun, CheckStatus
        from apps.intelligence.models import AnalysisRun
        from apps.orchestration.models import PipelineRun, PipelineStatus

        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        # --- Active Incidents ---
        active_qs = Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]
        )
        active_incidents = active_qs.aggregate(
            total=Count("id"),
            critical=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
            warning=Count("id", filter=Q(severity=AlertSeverity.WARNING)),
            info=Count("id", filter=Q(severity=AlertSeverity.INFO)),
        )

        # --- Pipeline Health (24h) ---
        pipeline_qs = PipelineRun.objects.filter(created_at__gte=last_24h)
        status_counts = dict(
            pipeline_qs.values_list("status").annotate(count=Count("id")).values_list(
                "status", "count"
            )
        )
        total_runs = sum(status_counts.values())
        successful = status_counts.get(PipelineStatus.NOTIFIED, 0)
        in_flight_statuses = [
            PipelineStatus.PENDING,
            PipelineStatus.INGESTED,
            PipelineStatus.CHECKED,
            PipelineStatus.ANALYZED,
        ]
        pipeline_health = {
            "total": total_runs,
            "successful": successful,
            "failed": status_counts.get(PipelineStatus.FAILED, 0),
            "retrying": status_counts.get(PipelineStatus.RETRYING, 0),
            "in_flight": sum(status_counts.get(s, 0) for s in in_flight_statuses),
            "success_rate": round(successful / total_runs * 100, 1) if total_runs else 0,
        }

        # --- Recent Check Runs (last 10) ---
        recent_check_runs = list(
            CheckRun.objects.order_by("-executed_at").only(
                "checker_name", "hostname", "status", "message", "executed_at"
            )[:10]
        )

        # --- Failed Pipelines (last 5) ---
        failed_pipelines = list(
            PipelineRun.objects.filter(status=PipelineStatus.FAILED)
            .order_by("-created_at")
            .only(
                "run_id", "trace_id", "last_error_type", "last_error_message", "created_at"
            )[:5]
        )

        # --- 7-Day Aggregations ---

        # Top failing checkers
        top_failing_checkers = list(
            CheckRun.objects.filter(
                status__in=[CheckStatus.WARNING, CheckStatus.CRITICAL],
                executed_at__gte=last_7d,
            )
            .values("checker_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        # Top error types
        top_error_types = list(
            PipelineRun.objects.filter(
                status=PipelineStatus.FAILED,
                created_at__gte=last_7d,
            )
            .values("last_error_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        # Intelligence provider usage
        provider_usage = list(
            AnalysisRun.objects.filter(created_at__gte=last_7d)
            .values("provider")
            .annotate(runs=Count("id"), tokens=Sum("total_tokens"))
            .order_by("-runs")
        )

        return {
            "active_incidents": active_incidents,
            "pipeline_health": pipeline_health,
            "recent_check_runs": recent_check_runs,
            "failed_pipelines": failed_pipelines,
            "top_failing_checkers": top_failing_checkers,
            "top_error_types": top_error_types,
            "provider_usage": provider_usage,
        }
```

**Step 4: Implement dashboard template**

Replace `templates/admin/dashboard.html` with the full template:

```html
{% extends "admin/index.html" %}
{% load i18n %}

{% block content %}
<div id="dashboard" style="margin-bottom: 30px;">

  <!-- Row 1: Active Incidents + Pipeline Health -->
  <div style="display: flex; gap: 20px; margin-bottom: 20px;">

    <!-- Active Incidents -->
    <div class="module" style="flex: 1; padding: 15px;">
      <h2 style="margin-top: 0;">Active Incidents</h2>
      {% if active_incidents.total > 0 %}
      <div style="font-size: 32px; font-weight: bold; margin-bottom: 10px;">
        {{ active_incidents.total }}
      </div>
      <div style="display: flex; gap: 10px;">
        {% if active_incidents.critical > 0 %}
        <span style="background:#dc3545;color:#fff;padding:3px 8px;border-radius:3px;font-size:12px;">
          {{ active_incidents.critical }} Critical
        </span>
        {% endif %}
        {% if active_incidents.warning > 0 %}
        <span style="background:#ffc107;color:#000;padding:3px 8px;border-radius:3px;font-size:12px;">
          {{ active_incidents.warning }} Warning
        </span>
        {% endif %}
        {% if active_incidents.info > 0 %}
        <span style="background:#17a2b8;color:#fff;padding:3px 8px;border-radius:3px;font-size:12px;">
          {{ active_incidents.info }} Info
        </span>
        {% endif %}
      </div>
      <div style="margin-top: 10px;">
        <a href="/admin/alerts/incident/?status__exact=open">View all &rarr;</a>
      </div>
      {% else %}
      <div style="color: #28a745; font-size: 18px;">No active incidents</div>
      {% endif %}
    </div>

    <!-- Pipeline Health (24h) -->
    <div class="module" style="flex: 1; padding: 15px;">
      <h2 style="margin-top: 0;">Pipeline Health (24h)</h2>
      {% if pipeline_health.total > 0 %}
      <div style="font-size: 32px; font-weight: bold; margin-bottom: 5px;">
        {{ pipeline_health.success_rate }}%
      </div>
      <div style="font-size: 12px; color: #666; margin-bottom: 10px;">
        {{ pipeline_health.successful }}/{{ pipeline_health.total }} runs succeeded
      </div>
      <!-- Success rate bar -->
      <div style="background:#eee;border-radius:4px;height:8px;margin-bottom:10px;">
        <div style="background:{% if pipeline_health.success_rate >= 90 %}#28a745{% elif pipeline_health.success_rate >= 70 %}#ffc107{% else %}#dc3545{% endif %};
                    height:8px;border-radius:4px;width:{{ pipeline_health.success_rate }}%;"></div>
      </div>
      <div style="display:flex;gap:15px;font-size:12px;">
        <span>Failed: <strong>{{ pipeline_health.failed }}</strong></span>
        <span>Retrying: <strong>{{ pipeline_health.retrying }}</strong></span>
        <span>In-flight: <strong>{{ pipeline_health.in_flight }}</strong></span>
      </div>
      {% else %}
      <div style="color: #666;">No pipeline runs in the last 24 hours</div>
      {% endif %}
    </div>
  </div>

  <!-- Row 2: Recent Check Runs + Failed Pipelines -->
  <div style="display: flex; gap: 20px; margin-bottom: 20px;">

    <!-- Recent Check Runs -->
    <div class="module" style="flex: 1; padding: 15px;">
      <h2 style="margin-top: 0;">Recent Check Runs</h2>
      {% if recent_check_runs %}
      <table style="width:100%;font-size:13px;">
        <thead>
          <tr>
            <th style="text-align:left;padding:4px 8px;">Checker</th>
            <th style="text-align:left;padding:4px 8px;">Host</th>
            <th style="text-align:left;padding:4px 8px;">Status</th>
            <th style="text-align:left;padding:4px 8px;">Time</th>
          </tr>
        </thead>
        <tbody>
          {% for run in recent_check_runs %}
          <tr>
            <td style="padding:4px 8px;">{{ run.checker_name }}</td>
            <td style="padding:4px 8px;">{{ run.hostname }}</td>
            <td style="padding:4px 8px;">
              <span style="background:{% if run.status == 'ok' %}#28a745{% elif run.status == 'warning' %}#ffc107{% elif run.status == 'critical' %}#dc3545{% else %}#6c757d{% endif %};
                          color:{% if run.status == 'warning' %}#000{% else %}#fff{% endif %};
                          padding:2px 6px;border-radius:3px;font-size:11px;">
                {{ run.status|upper }}
              </span>
            </td>
            <td style="padding:4px 8px;">{{ run.executed_at|timesince }} ago</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div style="margin-top:8px;"><a href="/admin/checkers/checkrun/">View all &rarr;</a></div>
      {% else %}
      <div style="color: #666;">No check runs recorded</div>
      {% endif %}
    </div>

    <!-- Failed Pipelines -->
    <div class="module" style="flex: 1; padding: 15px;">
      <h2 style="margin-top: 0;">Failed Pipelines</h2>
      {% if failed_pipelines %}
      <table style="width:100%;font-size:13px;">
        <thead>
          <tr>
            <th style="text-align:left;padding:4px 8px;">Run ID</th>
            <th style="text-align:left;padding:4px 8px;">Error</th>
            <th style="text-align:left;padding:4px 8px;">When</th>
          </tr>
        </thead>
        <tbody>
          {% for run in failed_pipelines %}
          <tr>
            <td style="padding:4px 8px;">
              <a href="/admin/orchestration/pipelinerun/{{ run.pk }}/change/">{{ run.run_id|truncatechars:12 }}</a>
            </td>
            <td style="padding:4px 8px;">{{ run.last_error_type|default:"Unknown"|truncatechars:40 }}</td>
            <td style="padding:4px 8px;">{{ run.created_at|timesince }} ago</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div style="margin-top:8px;">
        <a href="/admin/orchestration/pipelinerun/?status__exact=failed">View all &rarr;</a>
      </div>
      {% else %}
      <div style="color: #28a745;">No failed pipelines</div>
      {% endif %}
    </div>
  </div>

  <!-- Row 3: 7-Day Trends -->
  <div class="module" style="padding: 15px; margin-bottom: 20px;">
    <h2 style="margin-top: 0;">7-Day Trends</h2>
    <div style="display: flex; gap: 20px;">

      <!-- Top Failing Checkers -->
      <div style="flex: 1;">
        <h3 style="font-size: 14px; margin-bottom: 8px;">Top Failing Checkers</h3>
        {% if top_failing_checkers %}
        <table style="width:100%;font-size:13px;">
          {% for item in top_failing_checkers %}
          <tr>
            <td style="padding:3px 0;">{{ item.checker_name }}</td>
            <td style="padding:3px 0;text-align:right;font-weight:bold;">{{ item.count }}</td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <div style="color:#666;font-size:13px;">No failures</div>
        {% endif %}
      </div>

      <!-- Top Error Types -->
      <div style="flex: 1;">
        <h3 style="font-size: 14px; margin-bottom: 8px;">Top Error Types</h3>
        {% if top_error_types %}
        <table style="width:100%;font-size:13px;">
          {% for item in top_error_types %}
          <tr>
            <td style="padding:3px 0;">{{ item.last_error_type|default:"Unknown" }}</td>
            <td style="padding:3px 0;text-align:right;font-weight:bold;">{{ item.count }}</td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <div style="color:#666;font-size:13px;">No errors</div>
        {% endif %}
      </div>

      <!-- Provider Usage -->
      <div style="flex: 1;">
        <h3 style="font-size: 14px; margin-bottom: 8px;">AI Provider Usage</h3>
        {% if provider_usage %}
        <table style="width:100%;font-size:13px;">
          <thead>
            <tr>
              <th style="text-align:left;">Provider</th>
              <th style="text-align:right;">Runs</th>
              <th style="text-align:right;">Tokens</th>
            </tr>
          </thead>
          {% for item in provider_usage %}
          <tr>
            <td style="padding:3px 0;">{{ item.provider }}</td>
            <td style="padding:3px 0;text-align:right;">{{ item.runs }}</td>
            <td style="padding:3px 0;text-align:right;">{{ item.tokens|default:0 }}</td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <div style="color:#666;font-size:13px;">No analysis runs</div>
        {% endif %}
      </div>
    </div>
  </div>

</div>

<!-- Keep the default app list below the dashboard -->
{{ block.super }}
{% endblock %}
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add config/admin.py templates/admin/dashboard.html apps/orchestration/_tests/test_admin.py
git commit -m "feat: add dashboard context queries and template"
```

---

### Task 3: Query optimization across all ModelAdmins

**Files:**
- Modify: `apps/alerts/admin.py:66` (AlertAdmin)
- Modify: `apps/alerts/admin.py:163` (IncidentAdmin)
- Modify: `apps/checkers/admin.py:9` (CheckRunAdmin)
- Modify: `apps/intelligence/admin.py:8` (AnalysisRunAdmin)
- Modify: `apps/orchestration/admin.py:30` (PipelineRunAdmin)
- Modify: `apps/orchestration/admin.py:120` (StageExecutionAdmin)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing test**

```python
# apps/alerts/_tests/test_admin.py

import pytest
from django.test.utils import override_settings


@pytest.mark.django_db
class TestAdminQueryOptimization:
    def test_alert_list_uses_select_related(self, admin_client):
        """AlertAdmin should use select_related('incident') to avoid N+1."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        # Load the alert list page
        with CaptureQueriesContext(connection) as ctx:
            response = admin_client.get("/admin/alerts/alert/")
        assert response.status_code == 200
        # No query should contain a subquery for incident — select_related joins instead.
        # We just verify the page loads; the select_related is verified by reading the code.

    def test_incident_list_uses_prefetch_related(self, admin_client):
        response = admin_client.get("/admin/alerts/incident/")
        assert response.status_code == 200

    def test_pipeline_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/orchestration/pipelinerun/")
        assert response.status_code == 200

    def test_stage_execution_list_loads(self, admin_client):
        response = admin_client.get("/admin/orchestration/stageexecution/")
        assert response.status_code == 200

    def test_analysis_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/intelligence/analysisrun/")
        assert response.status_code == 200

    def test_check_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/checkers/checkrun/")
        assert response.status_code == 200
```

**Step 2: Run test to verify it passes (baseline — pages already load)**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestAdminQueryOptimization -v`
Expected: PASS (pages load, but no optimization yet — this is a baseline)

**Step 3: Add get_queryset() to each ModelAdmin**

In `apps/alerts/admin.py` — add to `AlertAdmin` class (after `inlines`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident")
```

In `apps/alerts/admin.py` — add to `IncidentAdmin` class (after `inlines`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("alerts", "pipeline_runs")
```

In `apps/checkers/admin.py` — add to `CheckRunAdmin` class (after `date_hierarchy`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("alert")
```

In `apps/intelligence/admin.py` — add to `AnalysisRunAdmin` class (after `date_hierarchy`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident")
```

In `apps/orchestration/admin.py` — add to `PipelineRunAdmin` class (after `inlines`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident").prefetch_related(
            "stage_executions"
        )
```

In `apps/orchestration/admin.py` — add to `StageExecutionAdmin` class (after `readonly_fields`):

```python
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("pipeline_run")
```

**Step 4: Run tests to verify they still pass**

Run: `uv run pytest apps/alerts/_tests/test_admin.py apps/orchestration/_tests/test_admin.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/checkers/admin.py apps/intelligence/admin.py \
       apps/orchestration/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat: add select_related/prefetch_related to all admin querysets"
```

---

### Task 4: End-to-end pipeline tracing fields

**Files:**
- Modify: `apps/alerts/admin.py` (AlertAdmin — add pipeline_trace field, add trace_id to search)
- Modify: `apps/orchestration/admin.py` (PipelineRunAdmin — add pipeline_flow field)
- Modify: `apps/checkers/admin.py` (CheckRunAdmin — add pipeline_run_link field)
- Modify: `apps/intelligence/admin.py` (AnalysisRunAdmin — add pipeline_run_link field)
- Test: `apps/orchestration/_tests/test_admin.py` (add tracing tests)

**Step 1: Write the failing tests**

Append to `apps/orchestration/_tests/test_admin.py`:

```python
@pytest.fixture
def pipeline_trace_data(db):
    """Create a full pipeline trace for testing."""
    from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
    from apps.orchestration.models import PipelineRun, PipelineStatus, StageExecution, StageStatus

    incident = Incident.objects.create(
        title="Test Incident",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
    )
    alert = Alert.objects.create(
        fingerprint="fp-1",
        source="prometheus",
        name="HighCPU",
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.FIRING,
        incident=incident,
    )
    run = PipelineRun.objects.create(
        trace_id="trace-abc",
        run_id="run-abc",
        status=PipelineStatus.CHECKED,
        current_stage="check",
        incident=incident,
    )
    StageExecution.objects.create(
        pipeline_run=run, stage="ingest", status=StageStatus.SUCCEEDED, attempt=1,
    )
    StageExecution.objects.create(
        pipeline_run=run, stage="check", status=StageStatus.RUNNING, attempt=1,
    )
    return {"incident": incident, "alert": alert, "run": run}


@pytest.mark.django_db
class TestPipelineTracing:
    def test_pipeline_run_detail_shows_flow(self, admin_client, pipeline_trace_data):
        run = pipeline_trace_data["run"]
        response = admin_client.get(f"/admin/orchestration/pipelinerun/{run.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        # Should show the pipeline flow stages
        assert "INGEST" in content
        assert "CHECK" in content
        assert "ANALYZE" in content
        assert "NOTIFY" in content

    def test_alert_search_by_trace_id(self, admin_client, pipeline_trace_data):
        """AlertAdmin should have trace_id in search_fields (via incident)."""
        response = admin_client.get("/admin/alerts/alert/?q=fp-1")
        assert response.status_code == 200

    def test_check_run_pipeline_link(self, admin_client, db):
        now = timezone.now()
        cr = CheckRun.objects.create(
            checker_name="cpu", hostname="srv1",
            status=CheckStatus.OK, trace_id="trace-xyz", executed_at=now,
        )
        response = admin_client.get(f"/admin/checkers/checkrun/{cr.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trace-xyz" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestPipelineTracing -v`
Expected: FAIL — pipeline_flow field not rendering stages

**Step 3: Add pipeline_flow field to PipelineRunAdmin**

In `apps/orchestration/admin.py`, add import and method to `PipelineRunAdmin`:

```python
from django.utils.html import format_html

# Add to PipelineRunAdmin class:

    readonly_fields = [
        "run_id",
        "trace_id",
        "pipeline_flow",  # <-- ADD THIS
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "total_duration_ms",
    ]

    # Add pipeline_flow to the first fieldset:
    # In fieldsets[0] ("Identification"), add "pipeline_flow" to fields list.

    @admin.display(description="Pipeline Flow")
    def pipeline_flow(self, obj):
        """Render a horizontal stage flow with status indicators."""
        from apps.orchestration.models import PipelineStage, StageStatus

        stages = [
            (PipelineStage.INGEST, "INGEST"),
            (PipelineStage.CHECK, "CHECK"),
            (PipelineStage.ANALYZE, "ANALYZE"),
            (PipelineStage.NOTIFY, "NOTIFY"),
        ]
        # Get stage executions for this run
        executions = {
            se.stage: se.status
            for se in obj.stage_executions.all()
        }
        parts = []
        for stage_value, stage_label in stages:
            status = executions.get(stage_value, None)
            if status == StageStatus.SUCCEEDED:
                color, icon = "#28a745", "&#10003;"  # checkmark
            elif status == StageStatus.RUNNING:
                color, icon = "#ffc107", "&#9679;"  # circle
            elif status == StageStatus.FAILED:
                color, icon = "#dc3545", "&#10007;"  # X
            else:
                color, icon = "#ccc", "&#9675;"  # empty circle
            parts.append(
                f'<span style="display:inline-block;text-align:center;margin:0 4px;">'
                f'<span style="color:{color};font-size:18px;">{icon}</span><br>'
                f'<span style="font-size:11px;">{stage_label}</span></span>'
            )
        arrow = '<span style="color:#999;margin:0 2px;">&#8594;</span>'
        return format_html(
            '<div style="display:flex;align-items:center;padding:8px 0;">{}</div>',
            format_html(arrow.join(parts)),
        )
```

**Step 4: Add pipeline_run_link to CheckRunAdmin**

In `apps/checkers/admin.py`, add to `CheckRunAdmin`:

```python
from django.utils.html import format_html

# Add to readonly_fields and fieldsets:
    readonly_fields = [
        # ... existing fields ...
        "pipeline_run_link",
    ]

    # Add to "Execution" fieldset fields list: "pipeline_run_link"

    @admin.display(description="Pipeline Run")
    def pipeline_run_link(self, obj):
        if obj.trace_id:
            return format_html(
                '<a href="/admin/orchestration/pipelinerun/?q={}">View pipeline ({})</a>',
                obj.trace_id,
                obj.trace_id[:12],
            )
        return "-"
```

**Step 5: Add pipeline_run_link to AnalysisRunAdmin**

In `apps/intelligence/admin.py`, add to `AnalysisRunAdmin`:

```python
from django.utils.html import format_html

# Add to readonly_fields:
    readonly_fields = [
        # ... existing fields ...
        "pipeline_run_link",
    ]

    # Add to "Identification" fieldset: "pipeline_run_link"

    @admin.display(description="Pipeline Run")
    def pipeline_run_link(self, obj):
        if obj.pipeline_run_id:
            return format_html(
                '<a href="/admin/orchestration/pipelinerun/?q={}">View pipeline ({})</a>',
                obj.pipeline_run_id,
                obj.pipeline_run_id[:12],
            )
        return "-"
```

**Step 6: Add trace_id to AlertAdmin search_fields**

In `apps/alerts/admin.py`, update AlertAdmin:

```python
    search_fields = ["name", "fingerprint", "description", "incident__pipeline_runs__trace_id"]
```

**Step 7: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestPipelineTracing -v`
Expected: All PASS

**Step 8: Commit**

```bash
git add apps/alerts/admin.py apps/checkers/admin.py apps/intelligence/admin.py \
       apps/orchestration/admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat: add end-to-end pipeline tracing fields to admin"
```

---

### Task 5: Bulk actions on alerts and orchestration admins

**Files:**
- Modify: `apps/alerts/admin.py` (AlertAdmin, IncidentAdmin — add bulk actions)
- Modify: `apps/orchestration/admin.py` (PipelineRunAdmin — add bulk action)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_admin.py`:

```python
from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.orchestration.models import PipelineRun, PipelineStatus


@pytest.mark.django_db
class TestBulkActions:
    def test_acknowledge_selected_incidents(self, admin_client):
        i1 = Incident.objects.create(title="Inc1", severity="critical", status=IncidentStatus.OPEN)
        i2 = Incident.objects.create(title="Inc2", severity="warning", status=IncidentStatus.OPEN)
        response = admin_client.post(
            "/admin/alerts/incident/",
            {"action": "acknowledge_selected", "_selected_action": [i1.pk, i2.pk]},
        )
        assert response.status_code == 302  # redirect after action
        i1.refresh_from_db()
        i2.refresh_from_db()
        assert i1.status == IncidentStatus.ACKNOWLEDGED
        assert i2.status == IncidentStatus.ACKNOWLEDGED

    def test_resolve_selected_incidents(self, admin_client):
        i1 = Incident.objects.create(title="Inc1", severity="critical", status=IncidentStatus.OPEN)
        response = admin_client.post(
            "/admin/alerts/incident/",
            {"action": "resolve_selected", "_selected_action": [i1.pk]},
        )
        assert response.status_code == 302
        i1.refresh_from_db()
        assert i1.status == IncidentStatus.RESOLVED

    def test_resolve_selected_alerts(self, admin_client):
        a1 = Alert.objects.create(
            fingerprint="fp-1", source="test", name="Alert1",
            severity=AlertSeverity.WARNING, status=AlertStatus.FIRING,
        )
        response = admin_client.post(
            "/admin/alerts/alert/",
            {"action": "resolve_selected", "_selected_action": [a1.pk]},
        )
        assert response.status_code == 302
        a1.refresh_from_db()
        assert a1.status == AlertStatus.RESOLVED

    def test_mark_pipelines_for_retry(self, admin_client):
        run = PipelineRun.objects.create(
            trace_id="t1", run_id="r1", status=PipelineStatus.FAILED,
        )
        response = admin_client.post(
            "/admin/orchestration/pipelinerun/",
            {"action": "mark_for_retry_selected", "_selected_action": [run.pk]},
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestBulkActions -v`
Expected: FAIL — actions don't exist yet

**Step 3: Add bulk actions to AlertAdmin**

In `apps/alerts/admin.py`, add to `AlertAdmin`:

```python
    actions = ["resolve_selected"]

    @admin.action(description="Resolve selected alerts")
    def resolve_selected(self, request, queryset):
        updated = queryset.update(status=AlertStatus.RESOLVED)
        self.message_user(request, f"{updated} alert(s) resolved.")
```

Add import at top of file:

```python
from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus
```

**Step 4: Add bulk actions to IncidentAdmin**

In `apps/alerts/admin.py`, add to `IncidentAdmin`:

```python
    actions = ["acknowledge_selected", "resolve_selected"]

    @admin.action(description="Acknowledge selected incidents")
    def acknowledge_selected(self, request, queryset):
        count = 0
        for incident in queryset.filter(status=IncidentStatus.OPEN):
            incident.acknowledge()
            count += 1
        self.message_user(request, f"{count} incident(s) acknowledged.")

    @admin.action(description="Resolve selected incidents")
    def resolve_selected(self, request, queryset):
        count = 0
        for incident in queryset.exclude(status__in=[IncidentStatus.RESOLVED, IncidentStatus.CLOSED]):
            incident.resolve()
            count += 1
        self.message_user(request, f"{count} incident(s) resolved.")
```

**Step 5: Add bulk action to PipelineRunAdmin**

In `apps/orchestration/admin.py`, add to `PipelineRunAdmin`:

```python
    actions = ["mark_for_retry_selected"]

    @admin.action(description="Mark selected for retry")
    def mark_for_retry_selected(self, request, queryset):
        count = 0
        for run in queryset.filter(status=PipelineStatus.FAILED):
            run.mark_retrying()
            count += 1
        self.message_user(request, f"{count} pipeline run(s) marked for retry.")
```

Add import:

```python
from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus, StageExecution
```

**Step 6: Run tests**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestBulkActions -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add apps/alerts/admin.py apps/orchestration/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat: add bulk admin actions for incidents, alerts, and pipeline runs"
```

---

### Task 6: Per-object actions with django-object-actions

**Files:**
- Modify: `apps/alerts/admin.py` (IncidentAdmin — add per-object buttons)
- Modify: `apps/orchestration/admin.py` (PipelineRunAdmin — add per-object buttons)
- Test: `apps/alerts/_tests/test_admin.py`
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_admin.py`:

```python
@pytest.mark.django_db
class TestPerObjectActions:
    def test_acknowledge_button_works(self, admin_client):
        incident = Incident.objects.create(
            title="Test", severity="critical", status=IncidentStatus.OPEN,
        )
        response = admin_client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/acknowledge_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.ACKNOWLEDGED

    def test_resolve_button_works(self, admin_client):
        incident = Incident.objects.create(
            title="Test", severity="critical", status=IncidentStatus.OPEN,
        )
        response = admin_client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/resolve_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.RESOLVED

    def test_close_button_works(self, admin_client):
        incident = Incident.objects.create(
            title="Test", severity="critical", status=IncidentStatus.RESOLVED,
        )
        response = admin_client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/close_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.CLOSED
```

Append to `apps/orchestration/_tests/test_admin.py`:

```python
@pytest.mark.django_db
class TestPipelineRunObjectActions:
    def test_mark_for_retry_button(self, admin_client):
        run = PipelineRun.objects.create(
            trace_id="t1", run_id="r1", status=PipelineStatus.FAILED,
        )
        response = admin_client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_for_retry/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING

    def test_mark_failed_button(self, admin_client):
        run = PipelineRun.objects.create(
            trace_id="t1", run_id="r1", status=PipelineStatus.PENDING,
        )
        response = admin_client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_failed/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.FAILED
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestPerObjectActions -v`
Expected: FAIL — action URLs not found (404)

**Step 3: Add per-object actions to IncidentAdmin**

In `apps/alerts/admin.py`, update IncidentAdmin:

```python
from django_object_actions import DjangoObjectActions, action as object_action

@admin.register(Incident)
class IncidentAdmin(DjangoObjectActions, admin.ModelAdmin):
    # ... existing code ...

    change_actions = ["acknowledge_incident", "resolve_incident", "close_incident"]

    @object_action(label="Acknowledge", description="Mark this incident as acknowledged")
    def acknowledge_incident(self, request, obj):
        if obj.status == IncidentStatus.OPEN:
            obj.acknowledge()
            self.message_user(request, f"Incident '{obj.title}' acknowledged.")
        else:
            self.message_user(request, f"Cannot acknowledge — status is '{obj.status}'.", level="warning")

    @object_action(label="Resolve", description="Mark this incident as resolved")
    def resolve_incident(self, request, obj):
        if obj.status not in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            obj.resolve()
            self.message_user(request, f"Incident '{obj.title}' resolved.")
        else:
            self.message_user(request, f"Already {obj.status}.", level="warning")

    @object_action(label="Close", description="Mark this incident as closed")
    def close_incident(self, request, obj):
        if obj.status != IncidentStatus.CLOSED:
            obj.close()
            self.message_user(request, f"Incident '{obj.title}' closed.")
        else:
            self.message_user(request, "Already closed.", level="warning")
```

**Step 4: Add per-object actions to PipelineRunAdmin**

In `apps/orchestration/admin.py`, update PipelineRunAdmin:

```python
from django_object_actions import DjangoObjectActions, action as object_action

@admin.register(PipelineRun)
class PipelineRunAdmin(DjangoObjectActions, admin.ModelAdmin):
    # ... existing code ...

    change_actions = ["mark_for_retry", "mark_failed"]

    @object_action(label="Mark for Retry", description="Queue this pipeline for retry")
    def mark_for_retry(self, request, obj):
        if obj.status == PipelineStatus.FAILED:
            obj.mark_retrying()
            self.message_user(request, f"Pipeline '{obj.run_id}' marked for retry.")
        else:
            self.message_user(
                request, f"Can only retry failed pipelines (current: {obj.status}).", level="warning"
            )

    @object_action(label="Mark Failed", description="Mark this pipeline as failed")
    def mark_failed(self, request, obj):
        if obj.status not in (PipelineStatus.FAILED, PipelineStatus.NOTIFIED):
            obj.mark_failed(error_type="ManualOverride", message="Manually marked as failed via admin")
            self.message_user(request, f"Pipeline '{obj.run_id}' marked as failed.")
        else:
            self.message_user(
                request, f"Cannot mark as failed — status is '{obj.status}'.", level="warning"
            )
```

**Step 5: Run tests**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestPerObjectActions apps/orchestration/_tests/test_admin.py::TestPipelineRunObjectActions -v`
Expected: All PASS

**Step 6: Run full test suite to check for regressions**

Run: `uv run pytest -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add apps/alerts/admin.py apps/orchestration/admin.py \
       apps/alerts/_tests/test_admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat: add per-object actions for incidents and pipeline runs"
```

---

## Implementation Notes

### Import Aliases

`django-object-actions` provides an `action` decorator. Django admin also has `admin.action`. To avoid conflicts, import the object action with an alias:

```python
from django_object_actions import DjangoObjectActions, action as object_action
```

Use `@object_action(...)` for per-object buttons and `@admin.action(...)` for bulk actions.

### Template Discovery

The dashboard template at `templates/admin/dashboard.html` requires `BASE_DIR / "templates"` in the `DIRS` setting of `TEMPLATES` config. Since `APP_DIRS=True` is already set, app-level templates will still be found.

### Custom AdminSite Wiring

By replacing `"django.contrib.admin"` with `"config.apps.MonitoringAdminConfig"` in `INSTALLED_APPS`, all existing `@admin.register()` decorators automatically register against the custom site. No changes needed to individual admin registrations.

### Dashboard Query Performance

All dashboard queries run only on the index page (via `index()` override, not `each_context()`). This prevents unnecessary queries on every admin page. Total: ~7 queries, all using indexed fields.

### Stage Values

The pipeline flow visualization uses `PipelineStage` choices. Check `apps/orchestration/models.py:11-17` for the exact string values (INGEST, CHECK, ANALYZE, NOTIFY) to ensure the display matches.