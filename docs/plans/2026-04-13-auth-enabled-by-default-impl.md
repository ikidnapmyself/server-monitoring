---
title: "Auth Enabled by Default Implementation Plan"
parent: Plans
---

# Auth Enabled by Default Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Flip `API_KEY_AUTH_ENABLED` default from disabled to enabled so all API endpoints are authenticated by default, matching ISO 27001 secure-by-default requirements.

**Architecture:** One setting change, two test file fixes, one new Django system check, and documentation updates. The system check warns when auth is disabled in a non-DEBUG environment.

**Tech Stack:** Django settings, Django system checks framework, pytest, `@override_settings`

---

### Task 1: Flip the default to enabled

**Files:**
- Modify: `config/settings.py:229`

**Step 1: Change the default**

In `config/settings.py`, change line 229 from:

```python
API_KEY_AUTH_ENABLED = os.environ.get("API_KEY_AUTH_ENABLED", "0") == "1"
```

to:

```python
API_KEY_AUTH_ENABLED = os.environ.get("API_KEY_AUTH_ENABLED", "1") == "1"
```

**Step 2: Verify Django starts**

Run: `uv run python manage.py check`
Expected: System check identified no issues (or only pre-existing warnings).

**Step 3: Commit**

```bash
git add config/settings.py
git commit -m "fix(security): enable API key authentication by default"
```

---

### Task 2: Fix notify view tests

**Files:**
- Modify: `apps/notify/_tests/test_views.py`

**Step 1: Add override_settings import and decorator**

At the top of `apps/notify/_tests/test_views.py`, the existing imports are:

```python
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
```

Add `override_settings` to the Django import:

```python
from django.test import Client, TestCase, override_settings
```

Then add `@override_settings(API_KEY_AUTH_ENABLED=False)` before each test class that hits API paths. The classes are:

- `NotifyViewPostTest`
- `NotifyViewGetTest`
- `NotifyBatchViewPostTest`
- `NotifyBatchViewGetTest`
- `DriversViewTest`

Add the decorator before each class definition, e.g.:

```python
@override_settings(API_KEY_AUTH_ENABLED=False)
class NotifyViewPostTest(TestCase):
```

Repeat for all 5 classes.

**Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/test_views.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add apps/notify/_tests/test_views.py
git commit -m "test: disable auth in notify view tests for unit isolation"
```

---

### Task 3: Fix webhook view tests

**Files:**
- Modify: `apps/alerts/_tests/views/test_webhook.py`

**Step 1: Add override_settings import and decorator**

At the top of `apps/alerts/_tests/views/test_webhook.py`, the existing imports are:

```python
from django.test import Client, TestCase
```

Add `override_settings`:

```python
from django.test import Client, TestCase, override_settings
```

Add `@override_settings(API_KEY_AUTH_ENABLED=False)` before each test class. The classes are:

- `WebhookViewTests`
- `WebhookViewPartialResponseTests`

```python
@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookViewTests(TestCase):
```

Repeat for both classes.

**Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/alerts/_tests/views/test_webhook.py -v`
Expected: All tests PASS

**Step 3: Run the full test suite to verify no regressions**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass, zero failures

**Step 4: Commit**

```bash
git add apps/alerts/_tests/views/test_webhook.py
git commit -m "test: disable auth in webhook view tests for unit isolation"
```

---

### Task 4: Add Django system check for auth disabled in production (TDD)

**Files:**
- Modify: `config/checks.py`
- Modify: `config/_tests/test_checks.py`

**Step 1: Write the failing tests**

Add to `config/_tests/test_checks.py`:

```python
class AuthDisabledCheckTests(SimpleTestCase):
    @override_settings(API_KEY_AUTH_ENABLED=False, DEBUG=False)
    def test_warns_when_auth_disabled_in_production(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 1
        assert errors[0].id == "config.W002"

    @override_settings(API_KEY_AUTH_ENABLED=False, DEBUG=True)
    def test_no_warning_in_debug_mode(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 0

    @override_settings(API_KEY_AUTH_ENABLED=True, DEBUG=False)
    def test_no_warning_when_auth_enabled(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest config/_tests/test_checks.py::AuthDisabledCheckTests -v`
Expected: FAIL — `ImportError: cannot import name 'check_auth_enabled'`

**Step 3: Write the implementation**

Add to `config/checks.py` after the existing `check_rate_limit_cache` function:

```python
@checks.register()
def check_auth_enabled(app_configs, **kwargs):
    errors = []
    if not getattr(settings, "API_KEY_AUTH_ENABLED", True):
        if not getattr(settings, "DEBUG", False):
            errors.append(
                checks.Warning(
                    "API key authentication is disabled (API_KEY_AUTH_ENABLED=False) "
                    "in a non-DEBUG environment. All API endpoints are unauthenticated. "
                    "Set API_KEY_AUTH_ENABLED=1 for production deployments.",
                    id="config.W002",
                )
            )
    return errors
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest config/_tests/test_checks.py -v`
Expected: All tests PASS (existing + new)

**Step 5: Verify Django picks up the check**

Run: `uv run python manage.py check`
Expected: No new warnings (because default is now enabled)

**Step 6: Commit**

```bash
git add config/checks.py config/_tests/test_checks.py
git commit -m "feat(security): add system check warning when auth disabled in production"
```

---

### Task 5: Update .env.sample and documentation

**Files:**
- Modify: `.env.sample`
- Modify: `docs/Security.md`
- Modify: `docs/Deployment.md`

**Step 1: Add API_KEY_AUTH_ENABLED to .env.sample**

Add after the `DJANGO_COLORS` line, before the `# Celery / Redis` section:

```bash
# API Key Authentication (enabled by default — set to 0 for local development)
API_KEY_AUTH_ENABLED=0
```

**Step 2: Update docs/Security.md**

Change the API key setup instructions (around line 107) from:

```markdown
1. Enable: set `API_KEY_AUTH_ENABLED=1` in your environment
```

to:

```markdown
1. API key authentication is **enabled by default**. To disable for local development, set `API_KEY_AUTH_ENABLED=0`.
```

**Step 3: Update docs/Deployment.md**

Change the env var table row (around line 35) from:

```markdown
| `API_KEY_AUTH_ENABLED` | `0` | No | Require API keys for endpoints |
```

to:

```markdown
| `API_KEY_AUTH_ENABLED` | `1` | No | API key auth (enabled by default; set `0` to disable for dev) |
```

**Step 4: Commit**

```bash
git add .env.sample docs/Security.md docs/Deployment.md
git commit -m "docs: update auth default to enabled in .env.sample and docs"
```

---

### Task 6: Final verification

**Step 1: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass, zero failures

**Step 2: Run linter and formatter**

Run: `uv run black . && uv run ruff check . --fix`
Expected: No issues

**Step 3: Run Django system checks**

Run: `uv run python manage.py check`
Expected: No new issues

**Step 4: Commit any formatting fixes**

```bash
git add -A
git commit -m "style: formatting fixes from black/ruff"
```