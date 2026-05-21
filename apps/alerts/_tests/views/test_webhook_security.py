"""Security tests for the alert webhook surface.

These tests guard the trust boundary around the in-process-only
:class:`apps.alerts.drivers.internal.InternalDriver`. Both the explicit
``/alerts/webhook/<driver>/`` HTTP path and the orchestrator service must
refuse ``driver="internal"`` unless a trusted in-process caller has
explicitly opted in via ``allow_internal=True``.

See ``apps/alerts/drivers/__init__.py`` and ``apps/alerts/services.py`` for
the dispatch gates these tests cover.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.alerts.models import Alert
from apps.alerts.services import AlertOrchestrator


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookInternalDriverSecurityTests(TestCase):
    """HTTP webhook surface MUST refuse driver=internal even with a well-formed
    payload. The internal driver is callable in-process only."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("alerts:webhook_driver", kwargs={"driver": "internal"})

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_post_webhook_with_driver_internal_returns_4xx(self):
        """A POST to /alerts/webhook/internal/ must be rejected (4xx) and
        must not create any Alert rows, regardless of payload well-formedness.
        """
        payload = {
            "source": "attacker",
            "fingerprint": "attacker-fp-1",
            "title": "Crafted internal",
            "severity": "critical",
            "labels": {"job": "evil"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        # The view should reject before any orchestration happens.
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        # And no Alert should have been created.
        self.assertFalse(Alert.objects.filter(fingerprint="attacker-fp-1").exists())


class OrchestratorInternalDriverGateTests(TestCase):
    """AlertOrchestrator.process_webhook must enforce the same gate as the HTTP
    surface: passing ``driver='internal'`` is refused unless the trusted caller
    explicitly opts in with ``allow_internal=True``."""

    def test_process_webhook_refuses_internal_driver_by_name(self):
        """Without the opt-in, process_webhook must refuse the internal driver.

        ``process_webhook`` wraps exceptions and records them on
        ``ProcessingResult.errors`` rather than re-raising — verify the error
        message reflects the deny path so we know the gate fired (and not, for
        example, a different validation error)."""
        orch = AlertOrchestrator()
        result = orch.process_webhook({"source": "x"}, driver="internal")

        self.assertTrue(result.has_errors)
        self.assertTrue(
            any("not webhook-reachable" in err for err in result.errors),
            f"Expected 'not webhook-reachable' error, got: {result.errors}",
        )
        # No alert should have been written.
        self.assertEqual(result.alerts_created, 0)

    def test_get_driver_refuses_internal_without_opt_in(self):
        """The underlying dispatcher must also refuse the internal driver
        without an explicit opt-in. This is the choke point that the view
        and the orchestrator both rely on."""
        from apps.alerts.drivers import get_driver

        with self.assertRaisesRegex(ValueError, "not webhook-reachable"):
            get_driver("internal")

    def test_process_webhook_allows_internal_driver_with_explicit_opt_in(self):
        """The escape hatch must work for trusted in-process callers (Task 4.2)
        — passing ``allow_internal=True`` should dispatch through InternalDriver
        and create an Alert from the supplied payload."""
        payload = {
            "source": "observability",
            "fingerprint": "heartbeat-stale:test-opt-in",
            "title": "Heartbeat stale: test-opt-in",
            "severity": "warning",
            "labels": {"job": "test"},
        }
        orch = AlertOrchestrator()
        # Should not raise and should not surface errors.
        result = orch.process_webhook(payload, driver="internal", allow_internal=True)

        self.assertFalse(result.has_errors, f"Unexpected errors: {result.errors}")
        self.assertTrue(Alert.objects.filter(fingerprint="heartbeat-stale:test-opt-in").exists())


def test_get_driver_unknown_name_still_raises():
    """Sanity check: the existing unknown-driver behaviour is preserved —
    the new gate is in addition to, not instead of, the registry lookup."""
    from apps.alerts.drivers import get_driver

    with pytest.raises(ValueError, match="Unknown driver"):
        get_driver("does-not-exist")
