# JSON Widget Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Beautify all JSON fields in Django admin using `django-json-widget` for editable fields and a `prettify_json` helper for readonly fields.

**Architecture:** Install `django-json-widget` and register it globally via `formfield_overrides` on each ModelAdmin that has editable JSONFields. For readonly JSON fields, add a shared `prettify_json` display method that renders `json.dumps(indent=2)` inside a styled `<pre>` block. The helper lives in `config/admin.py` alongside the custom AdminSite.

**Tech Stack:** django-json-widget, Django admin `formfield_overrides`, `format_html`

---

### Task 1: Install dependency and register in settings

**Files:**
- Modify: `pyproject.toml:7-17` (add dependency)
- Modify: `config/settings.py:49-62` (add to INSTALLED_APPS)

**Step 1: Add django-json-widget to pyproject.toml**

In `pyproject.toml`, add to the `dependencies` list:

```toml
    "django-json-widget>=2.0.1",
```

**Step 2: Install**

Run: `uv sync --extra dev`

**Step 3: Add to INSTALLED_APPS**

In `config/settings.py`, add `"django_json_widget"` after `"django_object_actions"` (line 52):

```python
INSTALLED_APPS = [
    "config.apps.MonitoringAdminConfig",
    "django_object_actions",
    "django_json_widget",
    "django.contrib.auth",
    ...
]
```

**Step 4: Verify Django starts**

Run: `uv run python manage.py check`
Expected: `System check identified no issues.`

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock config/settings.py
git commit -m "feat: add django-json-widget dependency"
```

---

### Task 2: Create shared prettify_json helper

**Files:**
- Modify: `config/admin.py` (add helper function)
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test**

Append to `apps/orchestration/_tests/test_admin.py`:

```python
@pytest.mark.django_db
class TestPrettifyJson:
    def test_prettify_json_renders_formatted(self):
        from config.admin import prettify_json

        data = {"key": "value", "nested": {"a": 1}}
        result = prettify_json(data)
        assert "&quot;key&quot;" in result or '"key"' in result
        assert "<pre" in result

    def test_prettify_json_empty_dict(self):
        from config.admin import prettify_json

        result = prettify_json({})
        assert "{}" in result

    def test_prettify_json_none(self):
        from config.admin import prettify_json

        result = prettify_json(None)
        assert "-" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestPrettifyJson -v`
Expected: FAIL — `prettify_json` not found

**Step 3: Add prettify_json to config/admin.py**

At the top of `config/admin.py`, add imports and the helper function (before the `MonitoringAdminSite` class):

```python
import json

from django.utils.html import format_html


def prettify_json(data):
    """Render a JSON-serializable value as a syntax-highlighted <pre> block."""
    if data is None:
        return "-"
    formatted = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return format_html(
        '<pre style="background:#f8f9fa;padding:10px;border-radius:4px;'
        'max-height:400px;overflow:auto;font-size:13px;margin:0;">{}</pre>',
        formatted,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestPrettifyJson -v`
Expected: PASS

**Step 5: Commit**

```bash
git add config/admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat: add prettify_json helper for admin JSON display"
```

---

### Task 3: Apply JSON widget to orchestration admin

**Files:**
- Modify: `apps/orchestration/admin.py` (add formfield_overrides + prettify display methods)
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test**

Append to `apps/orchestration/_tests/test_admin.py`:

```python
@pytest.mark.django_db
class TestJsonWidgetRendering:
    def test_pipeline_definition_config_uses_json_widget(self, admin_client):
        from apps.orchestration.models import PipelineDefinition

        pd = PipelineDefinition.objects.create(
            name="test", config={"stages": ["ingest"]}, is_active=True,
        )
        response = admin_client.get(
            f"/admin/orchestration/pipelinedefinition/{pd.pk}/change/"
        )
        assert response.status_code == 200
        content = response.content.decode()
        # django-json-widget injects its CSS/JS
        assert "json-editor" in content.lower() or "jsoneditor" in content.lower()

    def test_stage_execution_snapshot_pretty(self, admin_client):
        from apps.orchestration.models import (
            PipelineRun,
            StageExecution,
            StageStatus,
        )

        run = PipelineRun.objects.create(trace_id="t1", run_id="r1")
        se = StageExecution.objects.create(
            pipeline_run=run,
            stage="ingest",
            status=StageStatus.SUCCEEDED,
            attempt=1,
            output_snapshot={"result": "ok", "items": [1, 2, 3]},
        )
        response = admin_client.get(
            f"/admin/orchestration/stageexecution/{se.pk}/change/"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "<pre" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestJsonWidgetRendering -v`
Expected: FAIL — no json-editor class, no `<pre>` tag

**Step 3: Apply formfield_overrides and prettify methods**

In `apps/orchestration/admin.py`, add imports at the top:

```python
from django.db import models as db_models
from django_json_widget.widgets import JSONEditorWidget

from config.admin import prettify_json
```

In `PipelineDefinitionAdmin` (line 275), add after the class declaration:

```python
    formfield_overrides = {
        db_models.JSONField: {"widget": JSONEditorWidget},
    }
```

In `StageExecutionAdmin` (line 228), add after `readonly_fields`:

```python
    formfield_overrides = {
        db_models.JSONField: {"widget": JSONEditorWidget},
    }
```

In `StageExecutionAdmin`, add a display method and replace `"output_snapshot"` in the fieldsets "State" section (line 253) with `"pretty_output_snapshot"`. Also add `"pretty_output_snapshot"` to `readonly_fields`:

```python
    @admin.display(description="Output Snapshot")
    def pretty_output_snapshot(self, obj):
        return prettify_json(obj.output_snapshot)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py::TestJsonWidgetRendering -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/orchestration/admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat: add JSON widget to orchestration admin"
```

---

### Task 4: Apply JSON widget to alerts admin

**Files:**
- Modify: `apps/alerts/admin.py` (add prettify display methods for readonly JSON fields)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing test**

Append to `apps/alerts/_tests/test_admin.py`:

```python
@pytest.mark.django_db
class TestJsonPrettyDisplay:
    def test_alert_detail_shows_pretty_json(self, admin_client):
        from apps.alerts.models import Alert, AlertSeverity, AlertStatus

        alert = Alert.objects.create(
            fingerprint="fp-json",
            source="test",
            name="JsonTest",
            severity=AlertSeverity.WARNING,
            status=AlertStatus.FIRING,
            labels={"env": "prod", "team": "ops"},
            raw_payload={"alertname": "test", "nested": {"key": "val"}},
        )
        response = admin_client.get(f"/admin/alerts/alert/{alert.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "<pre" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestJsonPrettyDisplay -v`
Expected: FAIL — no `<pre>` in response

**Step 3: Add prettify display methods to AlertAdmin**

In `apps/alerts/admin.py`, add import at the top:

```python
from config.admin import prettify_json
```

In `AlertAdmin`, add `"pretty_labels"`, `"pretty_annotations"`, `"pretty_raw_payload"` to `readonly_fields`. Replace `"labels"`, `"annotations"`, `"raw_payload"` in the "Metadata" fieldset with the pretty versions. Add the display methods:

```python
    @admin.display(description="Labels")
    def pretty_labels(self, obj):
        return prettify_json(obj.labels)

    @admin.display(description="Annotations")
    def pretty_annotations(self, obj):
        return prettify_json(obj.annotations)

    @admin.display(description="Raw Payload")
    def pretty_raw_payload(self, obj):
        return prettify_json(obj.raw_payload)
```

In `IncidentAdmin`, add `"pretty_metadata"` to `readonly_fields`. Replace `"metadata"` in fieldsets with `"pretty_metadata"`. Add:

```python
    @admin.display(description="Metadata")
    def pretty_metadata(self, obj):
        return prettify_json(obj.metadata)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::TestJsonPrettyDisplay -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat: add JSON pretty display to alerts admin"
```

---

### Task 5: Apply JSON widget to remaining admins (checkers, intelligence, notify)

**Files:**
- Modify: `apps/checkers/admin.py`
- Modify: `apps/intelligence/admin.py`
- Modify: `apps/notify/admin.py`

**Step 1: Update checkers admin**

In `apps/checkers/admin.py`, add imports:

```python
from config.admin import prettify_json
```

Add `"pretty_metrics"` to `readonly_fields`, replace `"metrics"` in fieldsets with `"pretty_metrics"`. Add:

```python
    @admin.display(description="Metrics")
    def pretty_metrics(self, obj):
        return prettify_json(obj.metrics)
```

**Step 2: Update intelligence admin**

In `apps/intelligence/admin.py`, add imports:

```python
from django.db import models as db_models
from django_json_widget.widgets import JSONEditorWidget

from config.admin import prettify_json
```

In `AnalysisRunAdmin`, add:

```python
    formfield_overrides = {
        db_models.JSONField: {"widget": JSONEditorWidget},
    }
```

Add `"pretty_recommendations"`, `"pretty_provider_config"` to `readonly_fields`. Replace `"recommendations"` and `"provider_config"` in fieldsets with pretty versions. Add:

```python
    @admin.display(description="Recommendations")
    def pretty_recommendations(self, obj):
        return prettify_json(obj.recommendations)

    @admin.display(description="Provider Config")
    def pretty_provider_config(self, obj):
        return prettify_json(obj.provider_config)
```

**Step 3: Update notify admin**

In `apps/notify/admin.py`, add imports:

```python
from django.db import models as db_models
from django_json_widget.widgets import JSONEditorWidget
```

In `NotificationChannelAdmin`, add:

```python
    formfield_overrides = {
        db_models.JSONField: {"widget": JSONEditorWidget},
    }
```

**Step 4: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add apps/checkers/admin.py apps/intelligence/admin.py apps/notify/admin.py
git commit -m "feat: add JSON widget to checkers, intelligence, and notify admin"
```