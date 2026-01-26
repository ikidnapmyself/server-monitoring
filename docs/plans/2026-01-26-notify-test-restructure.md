# Notify App Test Restructure Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure notify app tests to mirror the source directory structure as specified in agents.md.

**Architecture:** Create a `tests/` directory that mirrors the source layout. Each source module gets a corresponding test file (e.g., `drivers/slack.py` → `tests/drivers/test_slack.py`). Remove the legacy `tests.py` file.

**Tech Stack:** pytest, pytest-django, pytest-cov

---

## Current State

```
apps/notify/
├── drivers/
│   ├── __init__.py
│   ├── base.py
│   ├── email.py
│   ├── generic.py
│   ├── pagerduty.py
│   └── slack.py
├── management/commands/
│   ├── list_notify_drivers.py
│   └── test_notify.py
├── models.py
├── services.py
├── templating.py
├── views.py
├── tests.py              ← Legacy empty file (to remove)
└── tests/
    └── test_slack_payload.py  ← Existing test (to reorganize)
```

## Target State

```
apps/notify/
├── tests/
│   ├── __init__.py
│   ├── drivers/
│   │   ├── __init__.py
│   │   ├── test_base.py
│   │   ├── test_email.py
│   │   ├── test_generic.py
│   │   ├── test_pagerduty.py
│   │   └── test_slack.py
│   ├── test_models.py
│   ├── test_services.py
│   └── test_templating.py
└── (no tests.py - removed)
```

---

### Task 1: Create test directory structure

**Files:**
- Create: `apps/notify/tests/__init__.py`
- Create: `apps/notify/tests/drivers/__init__.py`

**Step 1: Create __init__.py files**

```python
# apps/notify/tests/__init__.py
# (empty file)
```

```python
# apps/notify/tests/drivers/__init__.py
# (empty file)
```

**Step 2: Verify structure**

Run: `ls -la apps/notify/tests/`
Expected: Shows `__init__.py` and `drivers/` directory

**Step 3: Commit**

```bash
git add apps/notify/tests/__init__.py apps/notify/tests/drivers/__init__.py
git commit -m "chore(notify): create test directory structure"
```

---

### Task 2: Move and rename existing Slack test

**Files:**
- Move: `apps/notify/tests/test_slack_payload.py` → `apps/notify/tests/drivers/test_slack.py`

**Step 1: Move the file**

```bash
mv apps/notify/tests/test_slack_payload.py apps/notify/tests/drivers/test_slack.py
```

**Step 2: Run tests to verify they still pass**

Run: `uv run pytest apps/notify/tests/drivers/test_slack.py -v`
Expected: 2 tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/drivers/test_slack.py
git add apps/notify/tests/test_slack_payload.py  # stages deletion
git commit -m "refactor(notify): move slack tests to drivers/ subdirectory"
```

---

### Task 3: Create test_base.py for BaseNotifyDriver

**Files:**
- Create: `apps/notify/tests/drivers/test_base.py`
- Reference: `apps/notify/drivers/base.py`

**Step 1: Write the test file**

```python
"""Tests for BaseNotifyDriver and NotificationMessage."""

import pytest

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage


class TestNotificationMessage:
    """Tests for NotificationMessage dataclass."""

    def test_message_normalizes_severity(self):
        """Severity should be lowercased and validated."""
        msg = NotificationMessage(title="Test", message="Body", severity="WARNING")
        assert msg.severity == "warning"

    def test_message_invalid_severity_defaults_to_info(self):
        """Invalid severity should default to 'info'."""
        msg = NotificationMessage(title="Test", message="Body", severity="invalid")
        assert msg.severity == "info"

    def test_message_empty_severity_defaults_to_info(self):
        """Empty severity should default to 'info'."""
        msg = NotificationMessage(title="Test", message="Body", severity="")
        assert msg.severity == "info"

    def test_message_valid_severities(self):
        """All valid severities should be accepted."""
        for sev in ["critical", "warning", "info", "success"]:
            msg = NotificationMessage(title="Test", message="Body", severity=sev)
            assert msg.severity == sev

    def test_message_default_values(self):
        """Default values should be set correctly."""
        msg = NotificationMessage(title="Test", message="Body", severity="info")
        assert msg.channel == "default"
        assert msg.tags == {}
        assert msg.context == {}


class TestBaseNotifyDriver:
    """Tests for BaseNotifyDriver helper methods."""

    def test_message_to_dict(self):
        """_message_to_dict should convert message to dictionary."""
        msg = NotificationMessage(
            title="Test",
            message="Body",
            severity="warning",
            channel="ops",
            tags={"env": "prod"},
            context={"key": "value"},
        )

        class ConcreteDriver(BaseNotifyDriver):
            name = "test"

            def validate_config(self, config):
                return True

            def send(self, message, config):
                return {"success": True}

        driver = ConcreteDriver()
        result = driver._message_to_dict(msg)

        assert result["title"] == "Test"
        assert result["message"] == "Body"
        assert result["severity"] == "warning"
        assert result["channel"] == "ops"
        assert result["tags"] == {"env": "prod"}
        assert result["context"] == {"key": "value"}

    def test_severity_colors_defined(self):
        """SEVERITY_COLORS should have all severity levels."""
        assert "critical" in BaseNotifyDriver.SEVERITY_COLORS
        assert "warning" in BaseNotifyDriver.SEVERITY_COLORS
        assert "info" in BaseNotifyDriver.SEVERITY_COLORS
        assert "success" in BaseNotifyDriver.SEVERITY_COLORS

    def test_priority_map_defined(self):
        """PRIORITY_MAP should have all severity levels."""
        assert "critical" in BaseNotifyDriver.PRIORITY_MAP
        assert "warning" in BaseNotifyDriver.PRIORITY_MAP
        assert "info" in BaseNotifyDriver.PRIORITY_MAP
        assert "success" in BaseNotifyDriver.PRIORITY_MAP
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/drivers/test_base.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/drivers/test_base.py
git commit -m "test(notify): add tests for BaseNotifyDriver and NotificationMessage"
```

---

### Task 4: Create test_email.py for EmailNotifyDriver

**Files:**
- Create: `apps/notify/tests/drivers/test_email.py`
- Reference: `apps/notify/drivers/email.py`

**Step 1: Write the test file**

```python
"""Tests for EmailNotifyDriver."""

import pytest

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver


class TestEmailNotifyDriver:
    """Tests for EmailNotifyDriver."""

    @pytest.fixture
    def driver(self):
        return EmailNotifyDriver()

    @pytest.fixture
    def message(self):
        return NotificationMessage(
            title="Test Alert",
            message="Test message body",
            severity="warning",
            tags={"env": "test"},
            context={"key": "value"},
        )

    @pytest.fixture
    def valid_config(self):
        return {
            "smtp_host": "smtp.example.com",
            "from_address": "alerts@example.com",
            "to_addresses": ["ops@example.com"],
        }

    def test_driver_name(self, driver):
        """Driver should have correct name."""
        assert driver.name == "email"

    def test_validate_config_valid(self, driver, valid_config):
        """Valid config should pass validation."""
        assert driver.validate_config(valid_config) is True

    def test_validate_config_missing_smtp_host(self, driver):
        """Config without smtp_host should fail."""
        config = {"from_address": "alerts@example.com"}
        assert driver.validate_config(config) is False

    def test_validate_config_missing_from_address(self, driver):
        """Config without from_address should fail."""
        config = {"smtp_host": "smtp.example.com"}
        assert driver.validate_config(config) is False

    def test_build_email_subject_format(self, driver, message, valid_config):
        """Email subject should include severity and title."""
        email = driver._build_email(message, valid_config)
        assert email["Subject"] == "[WARNING] Test Alert"

    def test_build_email_from_address(self, driver, message, valid_config):
        """Email should use from_address from config."""
        email = driver._build_email(message, valid_config)
        assert email["From"] == "alerts@example.com"

    def test_build_email_to_addresses(self, driver, message, valid_config):
        """Email should use to_addresses from config."""
        email = driver._build_email(message, valid_config)
        assert "ops@example.com" in email["To"]

    def test_build_email_has_text_part(self, driver, message, valid_config):
        """Email should have plain text part from template."""
        email = driver._build_email(message, valid_config)
        parts = email.get_payload()
        content_types = [p.get_content_type() for p in parts]
        assert "text/plain" in content_types

    def test_build_email_has_html_part(self, driver, message, valid_config):
        """Email should have HTML part from template."""
        email = driver._build_email(message, valid_config)
        parts = email.get_payload()
        content_types = [p.get_content_type() for p in parts]
        assert "text/html" in content_types

    def test_build_email_priority_header(self, driver, message, valid_config):
        """Email should have X-Priority header based on severity."""
        email = driver._build_email(message, valid_config)
        assert email["X-Priority"] == "2"  # warning = priority 2

    def test_send_returns_error_for_invalid_config(self, driver, message):
        """Send should return error dict for invalid config."""
        result = driver.send(message, {})
        assert result["success"] is False
        assert "error" in result
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/drivers/test_email.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/drivers/test_email.py
git commit -m "test(notify): add tests for EmailNotifyDriver"
```

---

### Task 5: Create test_pagerduty.py for PagerDutyNotifyDriver

**Files:**
- Create: `apps/notify/tests/drivers/test_pagerduty.py`
- Reference: `apps/notify/drivers/pagerduty.py`

**Step 1: Write the test file**

```python
"""Tests for PagerDutyNotifyDriver."""

import json

import pytest

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver


class TestPagerDutyNotifyDriver:
    """Tests for PagerDutyNotifyDriver."""

    @pytest.fixture
    def driver(self):
        return PagerDutyNotifyDriver()

    @pytest.fixture
    def message(self):
        return NotificationMessage(
            title="Test Alert",
            message="Test message body",
            severity="critical",
            channel="ops",
            tags={"env": "prod", "service": "api"},
            context={"cpu_percent": 95},
        )

    @pytest.fixture
    def valid_config(self):
        return {"integration_key": "test-key-12345678901234567890"}

    def test_driver_name(self, driver):
        """Driver should have correct name."""
        assert driver.name == "pagerduty"

    def test_validate_config_valid(self, driver, valid_config):
        """Valid config should pass validation."""
        assert driver.validate_config(valid_config) is True

    def test_validate_config_missing_key(self, driver):
        """Config without integration_key should fail."""
        assert driver.validate_config({}) is False

    def test_validate_config_short_key(self, driver):
        """Config with too short integration_key should fail."""
        assert driver.validate_config({"integration_key": "short"}) is False

    def test_build_payload_has_routing_key(self, driver, message, valid_config):
        """Payload should include routing_key from config."""
        payload = driver._build_payload(message, valid_config)
        assert payload["routing_key"] == valid_config["integration_key"]

    def test_build_payload_has_event_action(self, driver, message, valid_config):
        """Payload should have event_action defaulting to trigger."""
        payload = driver._build_payload(message, valid_config)
        assert payload["event_action"] == "trigger"

    def test_build_payload_has_summary(self, driver, message, valid_config):
        """Payload should have summary with severity and title."""
        payload = driver._build_payload(message, valid_config)
        assert "[CRITICAL]" in payload["payload"]["summary"]
        assert "Test Alert" in payload["payload"]["summary"]

    def test_build_payload_severity_mapping(self, driver, valid_config):
        """Severity should be mapped to PagerDuty values."""
        for sev, expected in [
            ("critical", "critical"),
            ("warning", "warning"),
            ("info", "info"),
            ("success", "info"),
        ]:
            msg = NotificationMessage(title="Test", message="Body", severity=sev)
            payload = driver._build_payload(msg, valid_config)
            assert payload["payload"]["severity"] == expected

    def test_build_payload_uses_dedup_key_from_config(self, driver, message):
        """Payload should use dedup_key from config if provided."""
        config = {
            "integration_key": "test-key-12345678901234567890",
            "dedup_key": "custom-dedup",
        }
        payload = driver._build_payload(message, config)
        assert payload["dedup_key"] == "custom-dedup"

    def test_build_payload_uses_fingerprint_as_dedup(self, driver, valid_config):
        """Payload should use fingerprint tag as dedup_key if no dedup_key in config."""
        msg = NotificationMessage(
            title="Test",
            message="Body",
            severity="warning",
            tags={"fingerprint": "fp-123"},
        )
        payload = driver._build_payload(msg, valid_config)
        assert payload["dedup_key"] == "fp-123"

    def test_build_payload_json_serializable(self, driver, message, valid_config):
        """Payload should be JSON serializable."""
        payload = driver._build_payload(message, valid_config)
        # Remove incident key for serialization test (may have non-serializable datetime)
        payload_copy = {k: v for k, v in payload.items() if k != "incident"}
        dumped = json.dumps(payload_copy, default=str)
        assert dumped

    def test_send_returns_error_for_invalid_config(self, driver, message):
        """Send should return error dict for invalid config."""
        result = driver.send(message, {})
        assert result["success"] is False
        assert "error" in result
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/drivers/test_pagerduty.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/drivers/test_pagerduty.py
git commit -m "test(notify): add tests for PagerDutyNotifyDriver"
```

---

### Task 6: Create test_generic.py for GenericNotifyDriver

**Files:**
- Create: `apps/notify/tests/drivers/test_generic.py`
- Reference: `apps/notify/drivers/generic.py`

**Step 1: Write the test file**

```python
"""Tests for GenericNotifyDriver."""

import json

import pytest

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.generic import GenericNotifyDriver


class TestGenericNotifyDriver:
    """Tests for GenericNotifyDriver."""

    @pytest.fixture
    def driver(self):
        return GenericNotifyDriver()

    @pytest.fixture
    def message(self):
        return NotificationMessage(
            title="Test Alert",
            message="Test message body",
            severity="warning",
            channel="alerts",
            tags={"env": "test"},
            context={"key": "value"},
        )

    @pytest.fixture
    def valid_config(self):
        return {"endpoint": "https://example.com/webhook"}

    def test_driver_name(self, driver):
        """Driver should have correct name."""
        assert driver.name == "generic"

    def test_validate_config_with_endpoint(self, driver, valid_config):
        """Config with endpoint should pass validation."""
        assert driver.validate_config(valid_config) is True

    def test_validate_config_with_webhook_url(self, driver):
        """Config with webhook_url should pass validation."""
        config = {"webhook_url": "https://example.com/hook"}
        assert driver.validate_config(config) is True

    def test_validate_config_empty_is_valid(self, driver):
        """Empty config should be valid (disabled mode)."""
        assert driver.validate_config({}) is True

    def test_validate_config_disabled_is_valid(self, driver):
        """Config with disabled=True should be valid."""
        assert driver.validate_config({"disabled": True}) is True

    def test_validate_config_invalid_url(self, driver):
        """Config with non-http URL should fail."""
        config = {"endpoint": "ftp://example.com/webhook"}
        assert driver.validate_config(config) is False

    def test_build_payload_has_title(self, driver, message, valid_config):
        """Payload should include title."""
        payload = driver._build_payload(message, valid_config)
        assert payload["title"] == "Test Alert"

    def test_build_payload_has_message(self, driver, message, valid_config):
        """Payload should include message."""
        payload = driver._build_payload(message, valid_config)
        assert payload["message"] == "Test message body"

    def test_build_payload_has_severity(self, driver, message, valid_config):
        """Payload should include severity."""
        payload = driver._build_payload(message, valid_config)
        assert payload["severity"] == "warning"

    def test_build_payload_has_tags(self, driver, message, valid_config):
        """Payload should include tags."""
        payload = driver._build_payload(message, valid_config)
        assert payload["tags"] == {"env": "test"}

    def test_build_payload_has_incident(self, driver, message, valid_config):
        """Payload should include incident details."""
        payload = driver._build_payload(message, valid_config)
        assert "incident" in payload

    def test_build_payload_json_serializable(self, driver, message, valid_config):
        """Payload should be JSON serializable."""
        payload = driver._build_payload(message, valid_config)
        dumped = json.dumps(payload, default=str)
        assert dumped

    def test_send_returns_error_for_invalid_config(self, driver, message):
        """Send should return error dict for invalid URL config."""
        result = driver.send(message, {"endpoint": "invalid-url"})
        assert result["success"] is False
        assert "error" in result
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/drivers/test_generic.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/drivers/test_generic.py
git commit -m "test(notify): add tests for GenericNotifyDriver"
```

---

### Task 7: Create test_templating.py for templating module

**Files:**
- Create: `apps/notify/tests/test_templating.py`
- Reference: `apps/notify/templating.py`

**Step 1: Write the test file**

```python
"""Tests for notification templating module."""

import pytest

from apps.notify.templating import (
    NotificationTemplatingService,
    render_template,
)


class TestRenderTemplate:
    """Tests for render_template function."""

    def test_render_none_returns_none(self):
        """None spec should return None."""
        assert render_template(None, {}) is None

    def test_render_empty_string_returns_none(self):
        """Empty string spec should return None."""
        assert render_template("", {}) is None

    def test_render_inline_template(self):
        """Inline template should render with context."""
        result = render_template("Hello {{ name }}", {"name": "World"})
        assert result == "Hello World"

    def test_render_file_template(self):
        """File template should load and render."""
        ctx = {
            "title": "Test",
            "severity": "info",
            "message": "Test message",
            "tags": {},
            "context": {},
            "incident": {},
            "intelligence": None,
            "recommendations": [],
        }
        result = render_template("file:email_text.j2", ctx)
        assert "Test" in result
        assert "INFO" in result

    def test_render_file_without_prefix(self):
        """File template without 'file:' prefix should work if file exists."""
        ctx = {
            "title": "Test",
            "severity": "warning",
            "message": "Body",
            "tags": {},
            "context": {},
            "incident": {},
            "intelligence": None,
            "recommendations": [],
        }
        result = render_template("email_text.j2", ctx)
        assert "Test" in result

    def test_render_missing_file_raises(self):
        """Missing file template should raise ValueError."""
        with pytest.raises(ValueError, match="Template file not found"):
            render_template("file:nonexistent.j2", {})

    def test_render_dict_spec_inline(self):
        """Dict spec with type=inline should render inline template."""
        spec = {"type": "inline", "template": "Value: {{ x }}"}
        result = render_template(spec, {"x": 42})
        assert result == "Value: 42"

    def test_render_dict_spec_file(self):
        """Dict spec with type=file should load file template."""
        spec = {"type": "file", "template": "email_text.j2"}
        ctx = {
            "title": "Test",
            "severity": "info",
            "message": "Body",
            "tags": {},
            "context": {},
            "incident": {},
            "intelligence": None,
            "recommendations": [],
        }
        result = render_template(spec, ctx)
        assert "Test" in result


class TestNotificationTemplatingService:
    """Tests for NotificationTemplatingService."""

    @pytest.fixture
    def service(self):
        return NotificationTemplatingService()

    @pytest.fixture
    def message_dict(self):
        return {
            "title": "Test Alert",
            "message": "Alert body",
            "severity": "warning",
            "channel": "ops",
            "tags": {"env": "prod"},
            "context": {"key": "value"},
        }

    def test_compose_incident_details_has_metrics(self, service, message_dict):
        """Incident details should include system metrics."""
        result = service.compose_incident_details(message_dict, {})
        assert "cpu_count" in result
        assert "ram_total_bytes" in result
        assert "ram_total_human" in result

    def test_compose_incident_details_has_message_fields(self, service, message_dict):
        """Incident details should include message fields."""
        result = service.compose_incident_details(message_dict, {})
        assert result["title"] == "Test Alert"
        assert result["severity"] == "warning"

    def test_compose_incident_details_has_generated_at(self, service, message_dict):
        """Incident details should include timestamp."""
        result = service.compose_incident_details(message_dict, {})
        assert "generated_at" in result

    def test_build_template_context_has_top_level_fields(self, service, message_dict):
        """Template context should have top-level convenience fields."""
        incident = service.compose_incident_details(message_dict, {})
        ctx = service.build_template_context(message_dict, incident)

        assert ctx["title"] == "Test Alert"
        assert ctx["message"] == "Alert body"
        assert ctx["severity"] == "warning"
        assert "incident" in ctx

    def test_build_template_context_has_convenience_aliases(self, service, message_dict):
        """Template context should have convenience aliases."""
        message_dict["context"]["intelligence"] = {"summary": "Test summary"}
        incident = service.compose_incident_details(message_dict, {})
        ctx = service.build_template_context(message_dict, incident)

        assert "intelligence" in ctx
        assert "recommendations" in ctx
        assert "incident_id" in ctx

    def test_render_message_templates_uses_driver_default(self, service, message_dict):
        """Should use driver-default template file."""
        result = service.render_message_templates("email", message_dict, {})
        assert result["text"] is not None
        assert "Test Alert" in result["text"]

    def test_render_message_templates_raises_for_missing_driver(self, service, message_dict):
        """Should raise error when no template found for driver."""
        with pytest.raises(ValueError, match="No template found"):
            service.render_message_templates("nonexistent_driver", message_dict, {})
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/test_templating.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/test_templating.py
git commit -m "test(notify): add tests for templating module"
```

---

### Task 8: Create test_models.py for models

**Files:**
- Create: `apps/notify/tests/test_models.py`
- Reference: `apps/notify/models.py`

**Step 1: Write the test file**

```python
"""Tests for notify app models."""

import pytest

from apps.notify.models import NotificationChannel, NotificationSeverity


@pytest.mark.django_db
class TestNotificationChannel:
    """Tests for NotificationChannel model."""

    def test_create_channel(self):
        """Should create a notification channel."""
        channel = NotificationChannel.objects.create(
            name="test-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        assert channel.pk is not None
        assert channel.name == "test-slack"
        assert channel.driver == "slack"

    def test_channel_is_active_default(self):
        """Channel should be active by default."""
        channel = NotificationChannel.objects.create(
            name="test-channel",
            driver="email",
            config={},
        )
        assert channel.is_active is True

    def test_channel_str_representation(self):
        """Channel __str__ should return name."""
        channel = NotificationChannel(name="ops-alerts", driver="slack")
        assert str(channel) == "ops-alerts"

    def test_channel_unique_name(self):
        """Channel name should be unique."""
        NotificationChannel.objects.create(name="unique-name", driver="email", config={})
        with pytest.raises(Exception):  # IntegrityError
            NotificationChannel.objects.create(name="unique-name", driver="slack", config={})


class TestNotificationSeverity:
    """Tests for NotificationSeverity choices."""

    def test_severity_choices_exist(self):
        """Severity choices should be defined."""
        choices = NotificationSeverity.choices
        assert len(choices) >= 4

    def test_severity_values(self):
        """Severity should have expected values."""
        values = [c[0] for c in NotificationSeverity.choices]
        assert "critical" in values
        assert "warning" in values
        assert "info" in values
        assert "success" in values
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/tests/test_models.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests/test_models.py
git commit -m "test(notify): add tests for NotificationChannel model"
```

---

### Task 9: Remove legacy tests.py file

**Files:**
- Delete: `apps/notify/tests.py`

**Step 1: Remove the file**

```bash
rm apps/notify/tests.py
```

**Step 2: Run all tests to verify nothing breaks**

Run: `uv run pytest apps/notify/tests/ -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add apps/notify/tests.py  # stages deletion
git commit -m "chore(notify): remove legacy tests.py file"
```

---

### Task 10: Run coverage and verify improvement

**Files:**
- None (verification only)

**Step 1: Run tests with coverage**

Run: `uv run pytest apps/notify/tests/ --cov=apps/notify --cov-report=term-missing`
Expected: Coverage report showing improved coverage for notify app

**Step 2: Verify test structure matches source**

Run: `find apps/notify/tests -name "*.py" | sort`
Expected:
```
apps/notify/tests/__init__.py
apps/notify/tests/drivers/__init__.py
apps/notify/tests/drivers/test_base.py
apps/notify/tests/drivers/test_email.py
apps/notify/tests/drivers/test_generic.py
apps/notify/tests/drivers/test_pagerduty.py
apps/notify/tests/drivers/test_slack.py
apps/notify/tests/test_models.py
apps/notify/tests/test_templating.py
```

**Step 3: Final commit with all tests passing**

```bash
git status  # verify clean state
```

---

## Summary

After completing all tasks, the test structure will be:

| Source File | Test File |
|-------------|-----------|
| `drivers/base.py` | `tests/drivers/test_base.py` |
| `drivers/email.py` | `tests/drivers/test_email.py` |
| `drivers/generic.py` | `tests/drivers/test_generic.py` |
| `drivers/pagerduty.py` | `tests/drivers/test_pagerduty.py` |
| `drivers/slack.py` | `tests/drivers/test_slack.py` |
| `templating.py` | `tests/test_templating.py` |
| `models.py` | `tests/test_models.py` |

Coverage should improve significantly from the current 11% for the notify app.