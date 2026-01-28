# Intelligence App Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure `apps/intelligence` to follow the `agents.md` layout rules: split monolithic `views.py` into `views/` package with one module per endpoint, and move `tests.py` into `_tests/` directory mirroring the module structure.

**Architecture:** Split `views.py` (5 views) into separate modules under `views/`. Move `tests.py` into `_tests/` with subdirectories for `views/` and `providers/`. Keep backward-compatible import shims during transition.

**Tech Stack:** Django views, pytest, existing provider architecture

---

## Current State

**Problem:** Monolithic `views.py` (229 lines, 5 views) and `tests.py` (257 lines) violate `agents.md` layout rules.

**Current Layout:**
```
apps/intelligence/
├── views.py           # 5 views in one file (violates rules)
├── tests.py           # all tests in one file (violates rules)
├── providers/
│   ├── base.py
│   └── local.py
└── urls.py
```

**Target Layout (per agents.md):**
```
apps/intelligence/
├── views/
│   ├── __init__.py         # re-exports for backward compatibility
│   ├── recommendations.py  # RecommendationsView
│   ├── memory.py           # MemoryAnalysisView
│   ├── disk.py             # DiskAnalysisView
│   ├── providers.py        # ProvidersListView
│   └── health.py           # HealthView
├── _tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── views/
│   │   ├── __init__.py
│   │   ├── test_recommendations.py
│   │   ├── test_memory.py
│   │   ├── test_disk.py
│   │   ├── test_providers.py
│   │   └── test_health.py
│   └── providers/
│       ├── __init__.py
│       └── test_local.py
├── providers/
│   ├── base.py
│   └── local.py
└── urls.py                 # update imports
```

---

## Implementation Tasks

### Task 1: Create views directory structure

**Files:**
- Create: `apps/intelligence/views/__init__.py`

**Step 1: Create views package directory**

```bash
mkdir -p apps/intelligence/views
```

**Step 2: Create __init__.py with shim exports**

```python
# apps/intelligence/views/__init__.py
"""
Intelligence app views.

This package contains HTTP endpoints for intelligence recommendations.
Views are organized by endpoint/functionality.
"""

from apps.intelligence.views.disk import DiskAnalysisView
from apps.intelligence.views.health import HealthView
from apps.intelligence.views.memory import MemoryAnalysisView
from apps.intelligence.views.providers import ProvidersListView
from apps.intelligence.views.recommendations import RecommendationsView

__all__ = [
    "DiskAnalysisView",
    "HealthView",
    "MemoryAnalysisView",
    "ProvidersListView",
    "RecommendationsView",
]
```

**Step 3: Verify directory exists**

Run: `ls -la apps/intelligence/views/`
Expected: Shows `__init__.py`

**Step 4: Commit**

```bash
git add apps/intelligence/views/__init__.py
git commit -m "chore(intelligence): create views package structure"
```

---

### Task 2: Create shared view mixins module

**Files:**
- Create: `apps/intelligence/views/_mixins.py`

**Step 1: Write the mixins module**

```python
# apps/intelligence/views/_mixins.py
"""Shared mixins for intelligence views."""

from typing import Any

from django.http import JsonResponse


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data: Any, status: int = 200, safe: bool = True) -> JsonResponse:
        return JsonResponse(data, status=status, safe=safe)

    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)
```

**Step 2: Commit**

```bash
git add apps/intelligence/views/_mixins.py
git commit -m "chore(intelligence): add shared view mixins"
```

---

### Task 3: Create health.py view module

**Files:**
- Create: `apps/intelligence/views/health.py`

**Step 1: Write health view module**

```python
# apps/intelligence/views/health.py
"""Health check endpoint for the intelligence app."""

from django.views import View

from apps.intelligence.providers import list_providers
from apps.intelligence.views._mixins import JSONResponseMixin


class HealthView(JSONResponseMixin, View):
    """
    Health check endpoint for the intelligence app.

    GET /intelligence/health/
    """

    def get(self, request):
        """Return health status."""
        return self.json_response(
            {
                "status": "healthy",
                "app": "intelligence",
                "providers": list_providers(),
            }
        )
```

**Step 2: Verify import works**

Run: `uv run python -c "from apps.intelligence.views.health import HealthView; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/intelligence/views/health.py
git commit -m "feat(intelligence): add views/health.py module"
```

---

### Task 4: Create providers.py view module

**Files:**
- Create: `apps/intelligence/views/providers.py`

**Step 1: Write providers view module**

```python
# apps/intelligence/views/providers.py
"""Providers list endpoint for the intelligence app."""

from django.views import View

from apps.intelligence.providers import list_providers
from apps.intelligence.views._mixins import JSONResponseMixin


class ProvidersListView(JSONResponseMixin, View):
    """
    List available intelligence providers.

    GET /intelligence/providers/
    """

    def get(self, request):
        """List all registered providers."""
        providers = list_providers()
        return self.json_response(
            {
                "providers": providers,
                "count": len(providers),
            }
        )
```

**Step 2: Verify import works**

Run: `uv run python -c "from apps.intelligence.views.providers import ProvidersListView; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/intelligence/views/providers.py
git commit -m "feat(intelligence): add views/providers.py module"
```

---

### Task 5: Create memory.py view module

**Files:**
- Create: `apps/intelligence/views/memory.py`

**Step 1: Write memory view module**

```python
# apps/intelligence/views/memory.py
"""Memory analysis endpoint for the intelligence app."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class MemoryAnalysisView(JSONResponseMixin, View):
    """
    Analyze memory usage and get recommendations.

    GET /intelligence/memory/
        Returns top memory-consuming processes and recommendations.
    """

    def get(self, request):
        """Get memory analysis and recommendations."""
        top_n = int(request.GET.get("top_n", 10))

        try:
            provider = get_provider("local", top_n_processes=top_n)
            recommendations = provider._get_memory_recommendations()

            return self.json_response(
                {
                    "type": "memory",
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except Exception as e:
            return self.error_response(f"Error analyzing memory: {str(e)}", status=500)
```

**Step 2: Verify import works**

Run: `uv run python -c "from apps.intelligence.views.memory import MemoryAnalysisView; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/intelligence/views/memory.py
git commit -m "feat(intelligence): add views/memory.py module"
```

---

### Task 6: Create disk.py view module

**Files:**
- Create: `apps/intelligence/views/disk.py`

**Step 1: Write disk view module**

```python
# apps/intelligence/views/disk.py
"""Disk analysis endpoint for the intelligence app."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class DiskAnalysisView(JSONResponseMixin, View):
    """
    Analyze disk usage and get recommendations.

    GET /intelligence/disk/
        Returns large files, old logs, and recommendations.

    GET /intelligence/disk/?path=/var/log&threshold_mb=50&old_days=7
        Customize the analysis parameters.
    """

    def get(self, request):
        """Get disk analysis and recommendations."""
        path = request.GET.get("path", "/")
        threshold_mb = float(request.GET.get("threshold_mb", 100))
        old_days = int(request.GET.get("old_days", 30))

        try:
            provider = get_provider(
                "local",
                large_file_threshold_mb=threshold_mb,
                old_file_days=old_days,
            )
            recommendations = provider._get_disk_recommendations(path)

            return self.json_response(
                {
                    "type": "disk",
                    "path": path,
                    "threshold_mb": threshold_mb,
                    "old_days": old_days,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except Exception as e:
            return self.error_response(f"Error analyzing disk: {str(e)}", status=500)
```

**Step 2: Verify import works**

Run: `uv run python -c "from apps.intelligence.views.disk import DiskAnalysisView; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/intelligence/views/disk.py
git commit -m "feat(intelligence): add views/disk.py module"
```

---

### Task 7: Create recommendations.py view module

**Files:**
- Create: `apps/intelligence/views/recommendations.py`

**Step 1: Write recommendations view module**

```python
# apps/intelligence/views/recommendations.py
"""Recommendations endpoint for the intelligence app."""

import json

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class RecommendationsView(JSONResponseMixin, View):
    """
    Get recommendations based on system state or a specific incident.

    GET /intelligence/recommendations/
        Returns recommendations based on current system state.

    GET /intelligence/recommendations/?incident_id=<id>
        Returns recommendations for a specific incident.

    POST /intelligence/recommendations/
        Accepts JSON body with optional incident_id and provider config.
    """

    def get(self, request):
        """Get recommendations, optionally for a specific incident."""
        incident_id = request.GET.get("incident_id")
        provider_name = request.GET.get("provider", "local")

        try:
            if incident_id:
                # Import here to avoid circular imports
                from apps.alerts.models import Incident

                try:
                    incident = Incident.objects.get(id=incident_id)
                    provider = get_provider(provider_name)
                    recommendations = provider.analyze(incident)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                provider = get_provider(provider_name)
                recommendations = provider.get_recommendations()

            return self.json_response(
                {
                    "provider": provider_name,
                    "incident_id": incident_id,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)

    def post(self, request):
        """Get recommendations with custom configuration."""
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        incident_id = body.get("incident_id")
        provider_name = body.get("provider", "local")
        provider_config = body.get("config", {})

        try:
            provider = get_provider(provider_name, **provider_config)

            if incident_id:
                from apps.alerts.models import Incident

                try:
                    incident = Incident.objects.get(id=incident_id)
                    recommendations = provider.analyze(incident)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                recommendations = provider.get_recommendations()

            return self.json_response(
                {
                    "provider": provider_name,
                    "incident_id": incident_id,
                    "config": provider_config,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)
```

**Step 2: Verify import works**

Run: `uv run python -c "from apps.intelligence.views.recommendations import RecommendationsView; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/intelligence/views/recommendations.py
git commit -m "feat(intelligence): add views/recommendations.py module"
```

---

### Task 8: Update urls.py to use new views package

**Files:**
- Modify: `apps/intelligence/urls.py`

**Step 1: Update urls.py imports**

```python
# apps/intelligence/urls.py
"""
URL configuration for the intelligence app.
"""

from django.urls import path

from apps.intelligence.views import (
    DiskAnalysisView,
    HealthView,
    MemoryAnalysisView,
    ProvidersListView,
    RecommendationsView,
)

app_name = "intelligence"

urlpatterns = [
    # Health check
    path("health/", HealthView.as_view(), name="health"),
    # Providers
    path("providers/", ProvidersListView.as_view(), name="providers"),
    # Recommendations
    path("recommendations/", RecommendationsView.as_view(), name="recommendations"),
    # Specific analysis endpoints
    path("memory/", MemoryAnalysisView.as_view(), name="memory"),
    path("disk/", DiskAnalysisView.as_view(), name="disk"),
]
```

**Step 2: Verify URLs still work**

Run: `uv run python manage.py check`
Expected: `System check identified no issues`

**Step 3: Commit**

```bash
git add apps/intelligence/urls.py
git commit -m "refactor(intelligence): update urls.py to use views package"
```

---

### Task 9: Delete old views.py

**Files:**
- Delete: `apps/intelligence/views.py`

**Step 1: Remove old views.py**

```bash
rm apps/intelligence/views.py
```

**Step 2: Verify app still works**

Run: `uv run python manage.py check`
Expected: `System check identified no issues`

**Step 3: Commit**

```bash
git add -A
git commit -m "chore(intelligence): remove monolithic views.py"
```

---

### Task 10: Create _tests directory structure

**Files:**
- Create: `apps/intelligence/_tests/__init__.py`
- Create: `apps/intelligence/_tests/conftest.py`
- Create: `apps/intelligence/_tests/views/__init__.py`
- Create: `apps/intelligence/_tests/providers/__init__.py`

**Step 1: Create directory structure**

```bash
mkdir -p apps/intelligence/_tests/views apps/intelligence/_tests/providers
```

**Step 2: Create __init__.py files**

```python
# apps/intelligence/_tests/__init__.py
"""Intelligence app test suite."""
```

```python
# apps/intelligence/_tests/views/__init__.py
"""Tests for intelligence views."""
```

```python
# apps/intelligence/_tests/providers/__init__.py
"""Tests for intelligence providers."""
```

**Step 3: Create conftest.py**

```python
# apps/intelligence/_tests/conftest.py
"""Shared test fixtures for intelligence app."""

import pytest


@pytest.fixture
def local_provider():
    """Create a LocalRecommendationProvider instance for testing."""
    from apps.intelligence.providers import LocalRecommendationProvider

    return LocalRecommendationProvider(
        top_n_processes=5,
        large_file_threshold_mb=50.0,
        old_file_days=7,
    )
```

**Step 4: Commit**

```bash
git add apps/intelligence/_tests/
git commit -m "chore(intelligence): create _tests directory structure"
```

---

### Task 11: Create test_local.py for providers

**Files:**
- Create: `apps/intelligence/_tests/providers/test_local.py`

**Step 1: Write provider tests**

```python
# apps/intelligence/_tests/providers/test_local.py
"""Tests for the LocalRecommendationProvider."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.intelligence.providers import (
    LocalRecommendationProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


class TestProviderRegistry:
    """Tests for the provider registry."""

    def test_list_providers(self):
        """Test listing available providers."""
        from apps.intelligence.providers import list_providers

        providers = list_providers()
        assert "local" in providers

    def test_get_provider(self):
        """Test getting a provider by name."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local")
        assert isinstance(provider, LocalRecommendationProvider)

    def test_get_provider_with_config(self):
        """Test getting a provider with custom configuration."""
        from apps.intelligence.providers import get_provider

        provider = get_provider("local", top_n_processes=5)
        assert provider.top_n_processes == 5

    def test_get_unknown_provider_raises(self):
        """Test that getting an unknown provider raises KeyError."""
        from apps.intelligence.providers import get_provider

        with pytest.raises(KeyError):
            get_provider("unknown_provider")


class TestRecommendation:
    """Tests for the Recommendation dataclass."""

    def test_recommendation_to_dict(self):
        """Test converting recommendation to dictionary."""
        rec = Recommendation(
            type=RecommendationType.MEMORY,
            priority=RecommendationPriority.HIGH,
            title="Test Recommendation",
            description="Test description",
            details={"key": "value"},
            actions=["Action 1", "Action 2"],
            incident_id=123,
        )

        result = rec.to_dict()

        assert result["type"] == "memory"
        assert result["priority"] == "high"
        assert result["title"] == "Test Recommendation"
        assert result["description"] == "Test description"
        assert result["details"] == {"key": "value"}
        assert result["actions"] == ["Action 1", "Action 2"]
        assert result["incident_id"] == 123


class TestLocalRecommendationProvider:
    """Tests for the LocalRecommendationProvider."""

    def test_initialization_defaults(self):
        """Test provider initializes with default values."""
        provider = LocalRecommendationProvider()

        assert provider.top_n_processes == 10
        assert provider.large_file_threshold_mb == 100.0
        assert provider.old_file_days == 30

    def test_initialization_custom_values(self):
        """Test provider initializes with custom values."""
        provider = LocalRecommendationProvider(
            top_n_processes=5,
            large_file_threshold_mb=50.0,
            old_file_days=7,
        )

        assert provider.top_n_processes == 5
        assert provider.large_file_threshold_mb == 50.0
        assert provider.old_file_days == 7

    @patch("apps.intelligence.providers.local.psutil")
    def test_get_top_memory_processes(self, mock_psutil):
        """Test getting top memory-consuming processes."""
        mock_proc1 = MagicMock()
        mock_proc1.info = {
            "pid": 1234,
            "name": "python",
            "memory_percent": 15.5,
            "memory_info": MagicMock(rss=1024 * 1024 * 100),
            "cmdline": ["python", "test.py"],
        }

        mock_proc2 = MagicMock()
        mock_proc2.info = {
            "pid": 5678,
            "name": "nginx",
            "memory_percent": 5.0,
            "memory_info": MagicMock(rss=1024 * 1024 * 50),
            "cmdline": ["nginx"],
        }

        mock_psutil.process_iter.return_value = [mock_proc1, mock_proc2]

        provider = LocalRecommendationProvider()
        processes = provider._get_top_memory_processes()

        assert len(processes) > 0
        if len(processes) >= 2:
            assert processes[0].memory_percent >= processes[1].memory_percent

    def test_detect_incident_type_memory(self):
        """Test detecting memory incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "High Memory Usage Alert"
        incident.description = "Memory usage exceeded 90%"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "memory"

    def test_detect_incident_type_disk(self):
        """Test detecting disk incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "Disk Space Low"
        incident.description = "Storage running out on /var"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "disk"

    def test_detect_incident_type_cpu(self):
        """Test detecting CPU incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "High CPU Load"
        incident.description = "CPU usage at 95%"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "cpu"

    def test_detect_incident_type_unknown(self):
        """Test detecting unknown incident type."""
        provider = LocalRecommendationProvider()

        incident = MagicMock()
        incident.title = "General Alert"
        incident.description = "Something happened"
        incident.alerts = MagicMock()
        incident.alerts.all.return_value = []

        result = provider._detect_incident_type(incident)
        assert result == "unknown"

    def test_classify_file_log(self):
        """Test classifying log files."""
        provider = LocalRecommendationProvider()

        assert provider._classify_file(Path("/var/log/syslog.log")) == "log"
        assert provider._classify_file(Path("/var/log/app.log.1")) == "log"
        assert provider._classify_file(Path("/var/log/old.log.gz")) == "log"

    def test_classify_file_cache(self):
        """Test classifying cache files."""
        provider = LocalRecommendationProvider()

        assert provider._classify_file(Path("~/.cache/something")) == "cache"
        assert provider._classify_file(Path("/tmp/cache_file")) == "cache"

    def test_classify_file_temp(self):
        """Test classifying temp files."""
        provider = LocalRecommendationProvider()

        # Files in /tmp are classified as cache (due to 'tmp' in path)
        assert provider._classify_file(Path("/tmp/something.tmp")) == "cache"
        assert provider._classify_file(Path("/tmp/tmpfile")) == "cache"

    @patch("apps.intelligence.providers.local.psutil.virtual_memory")
    @patch("apps.intelligence.providers.local.psutil.disk_partitions")
    def test_get_recommendations_low_memory(self, mock_partitions, mock_memory):
        """Test get_recommendations when memory is high."""
        mock_memory.return_value = MagicMock(percent=85)
        mock_partitions.return_value = []

        provider = LocalRecommendationProvider()

        with patch.object(provider, "_get_memory_recommendations") as mock_mem_rec:
            mock_mem_rec.return_value = [
                Recommendation(
                    type=RecommendationType.MEMORY,
                    priority=RecommendationPriority.HIGH,
                    title="Test",
                    description="Test",
                )
            ]
            recommendations = provider.get_recommendations()

            mock_mem_rec.assert_called_once()
            assert len(recommendations) >= 1


@pytest.mark.django_db
class TestIntegration:
    """Integration tests requiring database access."""

    def test_analyze_with_incident(self):
        """Test analyzing a real incident."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        incident = Incident.objects.create(
            title="Memory Alert: High RAM Usage",
            description="Memory usage has exceeded 85% threshold",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )

        provider = LocalRecommendationProvider()
        recommendations = provider.analyze(incident)

        assert isinstance(recommendations, list)
        incident.delete()
```

**Step 2: Run tests to verify**

Run: `uv run pytest apps/intelligence/_tests/providers/test_local.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/intelligence/_tests/providers/test_local.py
git commit -m "test(intelligence): add _tests/providers/test_local.py"
```

---

### Task 12: Create view tests

**Files:**
- Create: `apps/intelligence/_tests/views/test_health.py`
- Create: `apps/intelligence/_tests/views/test_providers.py`

**Step 1: Write test_health.py**

```python
# apps/intelligence/_tests/views/test_health.py
"""Tests for the health view."""

import pytest
from django.test import Client


@pytest.mark.django_db
class TestHealthView:
    """Tests for HealthView."""

    def test_health_returns_ok(self):
        """Test health endpoint returns healthy status."""
        client = Client()
        response = client.get("/intelligence/health/")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["app"] == "intelligence"
        assert "providers" in data
```

**Step 2: Write test_providers.py**

```python
# apps/intelligence/_tests/views/test_providers.py
"""Tests for the providers list view."""

import pytest
from django.test import Client


@pytest.mark.django_db
class TestProvidersListView:
    """Tests for ProvidersListView."""

    def test_list_providers(self):
        """Test providers list endpoint."""
        client = Client()
        response = client.get("/intelligence/providers/")

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "count" in data
        assert "local" in data["providers"]
```

**Step 3: Run tests**

Run: `uv run pytest apps/intelligence/_tests/views/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add apps/intelligence/_tests/views/
git commit -m "test(intelligence): add view tests"
```

---

### Task 13: Delete old tests.py and verify

**Files:**
- Delete: `apps/intelligence/tests.py`

**Step 1: Remove old tests.py**

```bash
rm apps/intelligence/tests.py
```

**Step 2: Run all new tests**

Run: `uv run pytest apps/intelligence/_tests/ -v`
Expected: All tests PASS

**Step 3: Compare test counts**

Run: `uv run pytest apps/intelligence/_tests/ --collect-only -q | tail -1`
Expected: Shows test count (should be similar or greater than original)

**Step 4: Commit**

```bash
git add -A
git commit -m "chore(intelligence): remove monolithic tests.py after restructure"
```

---

### Task 14: Final verification

**Files:**
- All files in `apps/intelligence/`

**Step 1: Run Django check**

Run: `uv run python manage.py check`
Expected: `System check identified no issues`

**Step 2: Run all tests**

Run: `uv run pytest apps/intelligence/ -v`
Expected: All tests PASS

**Step 3: Verify directory structure**

Run: `find apps/intelligence -name "*.py" | sort`
Expected: Shows new structure matching target layout

---

## Verification Commands

After each task:
```bash
# Run Django check
uv run python manage.py check

# Run intelligence tests
uv run pytest apps/intelligence/_tests/ -v

# Run specific test file
uv run pytest apps/intelligence/_tests/providers/test_local.py -v
```

## Risk Assessment

- **Low risk:** Creating new view modules (Tasks 2-7) - additive changes
- **Medium risk:** Updating urls.py (Task 8) - must match new imports exactly
- **Low risk:** Deleting old files (Tasks 9, 13) - only after verification
- **Mitigation:** Run Django check and tests after each task