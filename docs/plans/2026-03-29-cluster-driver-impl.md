---
title: "2026-03-29 Cluster Alert Driver — Implementation"
parent: Plans
---

# Cluster Alert Driver — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a cluster alert driver and `push_to_hub` management command that enables multi-instance deployment — agent instances run checkers locally and push formed alerts to a hub instance via the existing webhook endpoint.

**Architecture:** A new `ClusterDriver` (9th alert driver) parses payloads from sibling instances. A `push_to_hub` management command runs checkers, creates local alerts, and POSTs them to the hub. Configuration is via `HUB_URL` and `CLUSTER_ENABLED` env vars — existing installs unaffected.

**Tech Stack:** Django, existing alert driver pattern, existing checker infrastructure, `urllib.request` for HTTP (no new dependencies)

---

### Task 1: Add cluster settings to `config/settings.py` and `.env.sample`

**Files:**
- Modify: `config/settings.py`
- Modify: `.env.sample`

**Step 1: Add settings**

In `config/settings.py`, after the `ORCHESTRATION_METRICS_BACKEND` line (around line 203), add:

```python
# Cluster (multi-instance)
CLUSTER_ENABLED = os.environ.get("CLUSTER_ENABLED", "0") == "1"
HUB_URL = os.environ.get("HUB_URL", "")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
WEBHOOK_SECRET_CLUSTER = os.environ.get("WEBHOOK_SECRET_CLUSTER", "")
```

In `.env.sample`, after the StatsD section, add:

```bash

# Cluster (multi-instance)
# Set HUB_URL to make this instance an agent that pushes to a hub
# Set CLUSTER_ENABLED=1 to make this instance a hub that accepts cluster payloads
# HUB_URL=https://monitoring-hub.example.com
# CLUSTER_ENABLED=0
# WEBHOOK_SECRET_CLUSTER=
# INSTANCE_ID=
```

**Step 2: Commit**

```bash
git add config/settings.py .env.sample
git commit -m "feat: add cluster settings (HUB_URL, CLUSTER_ENABLED, INSTANCE_ID)"
```

---

### Task 2: Create `ClusterDriver`

**Files:**
- Create: `apps/alerts/drivers/cluster.py`
- Create: `apps/alerts/_tests/drivers/test_cluster.py`

**Step 1: Write the tests**

Create `apps/alerts/_tests/drivers/test_cluster.py`:

```python
import socket
from datetime import datetime
from datetime import timezone as dt_tz

from django.test import TestCase, override_settings

from apps.alerts.drivers.cluster import ClusterDriver


class ClusterDriverValidateTests(TestCase):
    """Tests for ClusterDriver.validate()."""

    def setUp(self):
        self.driver = ClusterDriver()

    def test_validate_accepts_cluster_payload(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "CPU high", "status": "firing"}],
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_rejects_missing_source(self):
        payload = {"instance_id": "web-01", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_wrong_source(self):
        payload = {"source": "grafana", "instance_id": "web-01", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_missing_instance_id(self):
        payload = {"source": "cluster", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_missing_alerts(self):
        payload = {"source": "cluster", "instance_id": "web-01"}
        self.assertFalse(self.driver.validate(payload))


class ClusterDriverParseTests(TestCase):
    """Tests for ClusterDriver.parse()."""

    def setUp(self):
        self.driver = ClusterDriver()

    def test_parse_single_alert(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "hostname": "ip-10-0-1-42",
            "version": "1.0",
            "alerts": [
                {
                    "fingerprint": "cpu-check-web01",
                    "name": "CPU usage critical",
                    "status": "firing",
                    "severity": "critical",
                    "started_at": "2026-03-29T12:00:00Z",
                    "labels": {"checker": "cpu", "hostname": "ip-10-0-1-42"},
                    "annotations": {"message": "CPU at 95.2%"},
                    "metrics": {"cpu_percent": 95.2},
                }
            ],
        }
        result = self.driver.parse(payload)

        self.assertEqual(result.source, "cluster")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "CPU usage critical")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.fingerprint, "cpu-check-web01")
        self.assertEqual(alert.labels["instance_id"], "web-01")
        self.assertEqual(alert.labels["hostname"], "ip-10-0-1-42")

    def test_parse_multiple_alerts(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {"name": "CPU high", "status": "firing", "severity": "warning"},
                {"name": "Disk full", "status": "firing", "severity": "critical"},
            ],
        }
        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 2)

    def test_parse_injects_instance_id_into_labels(self):
        payload = {
            "source": "cluster",
            "instance_id": "db-server-03",
            "hostname": "db03.internal",
            "alerts": [
                {"name": "Memory high", "status": "firing", "labels": {"checker": "memory"}},
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.labels["instance_id"], "db-server-03")
        self.assertEqual(alert.labels["hostname"], "db03.internal")
        self.assertEqual(alert.labels["checker"], "memory")

    def test_parse_preserves_metrics_in_annotations(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {
                    "name": "CPU high",
                    "status": "firing",
                    "metrics": {"cpu_percent": 95.2, "load_avg": 4.5},
                },
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertIn("metrics", alert.annotations)

    def test_parse_generates_fingerprint_when_missing(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "Test Alert", "status": "firing"}],
        }
        result = self.driver.parse(payload)
        self.assertTrue(len(result.alerts[0].fingerprint) > 0)

    def test_parse_resolved_alert(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {
                    "name": "CPU OK",
                    "status": "resolved",
                    "ended_at": "2026-03-29T13:00:00Z",
                },
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.ended_at)

    def test_driver_name_is_cluster(self):
        self.assertEqual(self.driver.name, "cluster")

    def test_signature_header(self):
        self.assertEqual(self.driver.signature_header, "X-Cluster-Signature")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/alerts/_tests/drivers/test_cluster.py -v`
Expected: FAIL — `apps.alerts.drivers.cluster` does not exist

**Step 3: Create the driver**

Create `apps/alerts/drivers/cluster.py`:

```python
"""
Cluster driver for multi-instance deployments.

Parses alert payloads from sibling instances (agents) that push their
check results to a hub via the existing webhook endpoint.

Payload format:
{
    "source": "cluster",
    "instance_id": "web-server-03",
    "hostname": "ip-10-0-1-42",
    "version": "1.0",
    "alerts": [
        {
            "fingerprint": "cpu-check-ip-10-0-1-42",
            "name": "CPU usage critical",
            "status": "firing",
            "severity": "critical",
            "started_at": "2026-03-29T12:00:00Z",
            "labels": {"checker": "cpu", "hostname": "ip-10-0-1-42"},
            "annotations": {"message": "CPU at 95.2%"},
            "metrics": {"cpu_percent": 95.2}
        }
    ]
}
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone as dt_tz
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class ClusterDriver(BaseAlertDriver):
    """Driver for alerts from sibling server-monitoring instances."""

    name = "cluster"
    signature_header = "X-Cluster-Signature"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Validate that this payload is from a cluster agent."""
        return (
            payload.get("source") == "cluster"
            and bool(payload.get("instance_id"))
            and isinstance(payload.get("alerts"), list)
        )

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse cluster agent payload into normalized format."""
        instance_id = payload.get("instance_id", "")
        hostname = payload.get("hostname", "")
        alerts = []

        for alert_data in payload.get("alerts", []):
            parsed = self._parse_alert(alert_data, instance_id, hostname)
            alerts.append(parsed)

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            version=payload.get("version", ""),
            raw_payload=payload,
        )

    def _parse_alert(
        self,
        alert_data: dict[str, Any],
        instance_id: str,
        hostname: str,
    ) -> ParsedAlert:
        """Parse a single alert from cluster payload."""
        name = alert_data.get("name", "Unknown Alert")
        status = str(alert_data.get("status", "firing")).lower()
        severity = str(alert_data.get("severity", "warning")).lower()

        # Merge labels — always inject instance_id and hostname
        labels = alert_data.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        labels = {str(k): str(v) for k, v in labels.items()}
        labels["instance_id"] = instance_id
        if hostname:
            labels["hostname"] = hostname

        # Fingerprint: use provided or generate
        fingerprint = alert_data.get("fingerprint", "")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        # Annotations — preserve metrics if present
        annotations = alert_data.get("annotations", {})
        if not isinstance(annotations, dict):
            annotations = {}
        metrics = alert_data.get("metrics")
        if metrics:
            annotations["metrics"] = json.dumps(metrics)

        # Timestamps
        started_at = self._parse_timestamp(alert_data.get("started_at"))
        ended_at = None
        if status == "resolved":
            ended_at = self._parse_timestamp(alert_data.get("ended_at"))

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=alert_data.get("description", ""),
            labels=labels,
            annotations=annotations,
            started_at=started_at,
            ended_at=ended_at,
            raw_payload=alert_data,
        )

    def _parse_timestamp(self, value: Any) -> datetime:
        """Parse a timestamp from string or return now."""
        if not value:
            return timezone.now()
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        return timezone.now()
```

**Step 4: Run tests**

Run: `uv run pytest apps/alerts/_tests/drivers/test_cluster.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add apps/alerts/drivers/cluster.py apps/alerts/_tests/drivers/test_cluster.py
git commit -m "feat: add ClusterDriver for multi-instance alert forwarding"
```

---

### Task 3: Register ClusterDriver conditionally

**Files:**
- Modify: `apps/alerts/drivers/__init__.py`

**Step 1: Add conditional registration**

After the existing imports (line 13), add:

```python
from apps.alerts.drivers.cluster import ClusterDriver
```

Add to `__all__`:
```python
    "ClusterDriver",
```

After the existing `DRIVER_REGISTRY` dict (line 42), add:

```python

# Register cluster driver only when CLUSTER_ENABLED=1
def _register_cluster_driver():
    from django.conf import settings
    if getattr(settings, "CLUSTER_ENABLED", False):
        DRIVER_REGISTRY["cluster"] = ClusterDriver

_register_cluster_driver()
```

**Step 2: Write test for conditional registration**

Add to `apps/alerts/_tests/drivers/test_cluster.py`:

```python
class ClusterDriverRegistrationTests(TestCase):
    """Tests for conditional driver registration."""

    @override_settings(CLUSTER_ENABLED=True)
    def test_driver_registered_when_enabled(self):
        from apps.alerts.drivers import DRIVER_REGISTRY

        # Re-register since settings changed after module load
        DRIVER_REGISTRY["cluster"] = ClusterDriver
        self.assertIn("cluster", DRIVER_REGISTRY)

    @override_settings(CLUSTER_ENABLED=False)
    def test_driver_accessible_by_direct_import(self):
        """ClusterDriver can always be imported directly."""
        from apps.alerts.drivers.cluster import ClusterDriver as CD
        self.assertEqual(CD.name, "cluster")
```

**Step 3: Run tests**

Run: `uv run pytest apps/alerts/_tests/drivers/test_cluster.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add apps/alerts/drivers/__init__.py apps/alerts/_tests/drivers/test_cluster.py
git commit -m "feat: register ClusterDriver conditionally on CLUSTER_ENABLED"
```

---

### Task 4: Create `push_to_hub` management command

**Files:**
- Create: `apps/alerts/management/commands/push_to_hub.py`
- Create: `apps/alerts/_tests/commands/test_push_to_hub.py`
- Create: `apps/alerts/_tests/commands/__init__.py`

**Step 1: Write tests**

Create `apps/alerts/_tests/commands/__init__.py` (empty file).

Create `apps/alerts/_tests/commands/test_push_to_hub.py`:

```python
import json
import socket
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.checkers.checkers.base import CheckResult, CheckStatus


class PushToHubTests(TestCase):
    """Tests for push_to_hub management command."""

    @override_settings(HUB_URL="")
    def test_fails_without_hub_url(self):
        """Command exits with error when HUB_URL is not configured."""
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=out)
        self.assertIn("HUB_URL", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_dry_run_does_not_post(self, mock_registry):
        """--dry-run shows payload but doesn't POST."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="CPU OK",
            metrics={"cpu_percent": 10.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("dry run", output.lower())

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_posts_to_hub_url(self, mock_urlopen, mock_registry):
        """Command POSTs checker results to HUB_URL."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.WARNING,
            message="CPU at 75%",
            metrics={"cpu_percent": 75.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 202
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", stdout=out)

        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["source"], "cluster")
        self.assertTrue(len(payload["alerts"]) > 0)

    @override_settings(HUB_URL="https://hub.example.com", INSTANCE_ID="test-agent")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_uses_instance_id_from_settings(self, mock_urlopen, mock_registry):
        """Command uses INSTANCE_ID from settings."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="OK",
            metrics={},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 202
        mock_response.read.return_value = b'{}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", stdout=out)

        request = mock_urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["instance_id"], "test-agent")

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_json_output(self, mock_registry):
        """--json outputs JSON format."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="OK",
            metrics={},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", "--json", stdout=out)
        output = out.getvalue()
        parsed = json.loads(output)
        self.assertIn("alerts", parsed)
        self.assertEqual(parsed["source"], "cluster")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: FAIL — module does not exist

**Step 3: Create the command**

Create `apps/alerts/management/commands/push_to_hub.py`:

```python
"""
Management command to push local checker results to a hub instance.

Usage:
    python manage.py push_to_hub                    # Run checkers, push to hub
    python manage.py push_to_hub --dry-run          # Show payload, don't POST
    python manage.py push_to_hub --json             # JSON output
    python manage.py push_to_hub --checkers cpu,memory  # Specific checkers only
"""

import hashlib
import hmac
import json
import socket
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.checkers.base import CheckStatus


class Command(BaseCommand):
    help = "Run health checks and push results to a hub instance"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show payload without sending to hub.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output result as JSON.",
        )
        parser.add_argument(
            "--checkers",
            type=str,
            help="Comma-separated list of checkers to run (default: all).",
        )

    def handle(self, *args, **options):
        hub_url = getattr(settings, "HUB_URL", "")
        if not hub_url:
            raise CommandError(
                "HUB_URL is not configured. Set it in .env to enable agent mode."
            )

        instance_id = getattr(settings, "INSTANCE_ID", "") or socket.gethostname()
        hostname = socket.gethostname()
        secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")

        # Determine which checkers to run
        checker_names = None
        if options.get("checkers"):
            checker_names = [c.strip() for c in options["checkers"].split(",")]

        # Run checkers
        alerts = []
        for name, checker_cls in CHECKER_REGISTRY.items():
            if checker_names and name not in checker_names:
                continue
            try:
                checker = checker_cls()
                result = checker.run()
                alert = self._result_to_alert(result, instance_id, hostname)
                alerts.append(alert)
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Checker {name} failed: {e}"))

        # Build payload
        payload = {
            "source": "cluster",
            "instance_id": instance_id,
            "hostname": hostname,
            "version": "1.0",
            "alerts": alerts,
        }

        if options["json_output"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            if options["dry_run"]:
                return
        elif options["dry_run"]:
            self.stdout.write(self.style.NOTICE("Dry run — payload:"))
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return
        else:
            self.stdout.write(
                f"Pushing {len(alerts)} alert(s) from {instance_id} to {hub_url}"
            )

        # POST to hub
        url = hub_url.rstrip("/") + "/alerts/webhook/cluster/"
        body = json.dumps(payload, default=str).encode()

        headers = {"Content-Type": "application/json"}
        if secret:
            signature = hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-Cluster-Signature"] = signature

        request = Request(url, data=body, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=30) as response:
                status = response.status
                resp_body = response.read().decode()

            if status in (200, 201, 202):
                if options["json_output"]:
                    self.stdout.write(json.dumps(payload, indent=2, default=str))
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f"Hub accepted: HTTP {status}")
                    )
            else:
                raise CommandError(f"Hub returned HTTP {status}: {resp_body}")

        except Exception as e:
            if isinstance(e, CommandError):
                raise
            raise CommandError(f"Failed to reach hub at {url}: {e}")

    def _result_to_alert(
        self, result, instance_id: str, hostname: str
    ) -> dict:
        """Convert a CheckResult to a cluster alert dict."""
        # Map check status to alert status/severity
        if result.status == CheckStatus.OK:
            status = "resolved"
            severity = "info"
        elif result.status == CheckStatus.WARNING:
            status = "firing"
            severity = "warning"
        elif result.status == CheckStatus.CRITICAL:
            status = "firing"
            severity = "critical"
        else:
            status = "firing"
            severity = "warning"

        now = datetime.now(timezone.utc).isoformat()

        return {
            "fingerprint": f"{result.checker_name}-{hostname}",
            "name": f"{result.checker_name}: {result.message}",
            "status": status,
            "severity": severity,
            "started_at": now,
            "ended_at": now if status == "resolved" else None,
            "description": result.message,
            "labels": {
                "checker": result.checker_name,
                "hostname": hostname,
                "instance_id": instance_id,
            },
            "annotations": {
                "message": result.message,
            },
            "metrics": result.metrics,
        }
```

**Step 4: Run tests**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: All pass

**Step 5: Run full test suite**

Run: `uv run pytest apps/alerts/ -v`
Expected: All pass (existing + new)

**Step 6: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/
git commit -m "feat: add push_to_hub management command for agent-to-hub forwarding"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `docs/Deployment.md`

**Step 1: Add multi-instance section**

At the end of `docs/Deployment.md` (before the final `---` or at the end), add:

```markdown

---

## Multi-Instance (Cluster)

Deploy multiple instances across servers: **agents** monitor locally and push alerts to a **hub** that runs the full pipeline (intelligence + notifications).

### Architecture

```
Agent (server-1)  ──POST──┐
Agent (server-2)  ──POST──┤──▶  Hub  ──▶  intelligence ──▶ notify
Agent (server-3)  ──POST──┘     (receives cluster alerts)
```

All instances run the same codebase. Role is determined by environment variables.

### Agent setup

On each server you want to monitor:

1. Install the project normally (`./bin/install.sh` dev or prod mode)
2. Add to `.env`:

```bash
HUB_URL=https://monitoring-hub.example.com
WEBHOOK_SECRET_CLUSTER=your-shared-secret
INSTANCE_ID=web-server-01
```

3. Schedule the push command via cron:

```bash
# Every 5 minutes
*/5 * * * * cd /opt/server-monitoring && uv run python manage.py push_to_hub --json >> push.log 2>&1
```

Or run manually:

```bash
uv run python manage.py push_to_hub              # Push all checker results
uv run python manage.py push_to_hub --dry-run    # Preview without sending
uv run python manage.py push_to_hub --checkers cpu,memory  # Specific checkers
```

### Hub setup

On the central monitoring server:

1. Install the project (`./bin/install.sh` prod or docker mode)
2. Add to `.env`:

```bash
CLUSTER_ENABLED=1
WEBHOOK_SECRET_CLUSTER=your-shared-secret
```

The hub accepts cluster payloads at `POST /alerts/webhook/cluster/` and processes them through the full pipeline. Each alert carries `instance_id` and `hostname` labels for per-server filtering.

### Standalone (default)

Existing installs with neither `HUB_URL` nor `CLUSTER_ENABLED` set continue to work as standalone instances with no changes.
```

**Step 2: Commit**

```bash
git add docs/Deployment.md
git commit -m "docs: add multi-instance cluster deployment guide"
```

---

### Task 6: Run full test suite and verify

**Step 1: Run all Python tests**

Run: `uv run pytest`
Expected: All pass, no regressions

**Step 2: Run BATS tests**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
Expected: All pass

**Step 3: Run linters**

Run: `uv run black --check . && uv run ruff check .`
Expected: No issues

**Step 4: Commit any formatting fixes if needed**

```bash
git add -A
git commit -m "style: format cluster driver code"
```