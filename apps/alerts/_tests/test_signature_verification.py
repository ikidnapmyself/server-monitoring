"""Tests for webhook signature verification."""

import hashlib
import hmac
import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from apps.alerts.drivers.base import BaseAlertDriver
from apps.alerts.drivers.generic import GenericWebhookDriver
from apps.alerts.drivers.grafana import GrafanaDriver
from apps.alerts.drivers.newrelic import NewRelicDriver
from apps.alerts.drivers.pagerduty import PagerDutyDriver


class BaseDriverSignatureTests(TestCase):
    def test_base_driver_defaults(self):
        assert BaseAlertDriver.signature_header is None
        assert BaseAlertDriver.signature_algorithm == "sha256"

    def test_verify_signature_valid(self):
        driver = GenericWebhookDriver()
        body = b'{"test": true}'
        secret = "my-secret"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert driver.verify_signature(body, expected, secret) is True

    def test_verify_signature_invalid(self):
        driver = GenericWebhookDriver()
        assert driver.verify_signature(b"body", "wrong-sig", "secret") is False

    def test_verify_signature_sha256_prefix(self):
        driver = GenericWebhookDriver()
        body = b'{"test": true}'
        secret = "my-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert driver.verify_signature(body, f"sha256={digest}", secret) is True


class DriverSignatureHeaderTests(TestCase):
    def test_grafana_header(self):
        assert GrafanaDriver.signature_header == "X-Grafana-Signature"

    def test_pagerduty_header(self):
        assert PagerDutyDriver.signature_header == "X-PagerDuty-Signature"

    def test_newrelic_header(self):
        assert NewRelicDriver.signature_header == "X-NewRelic-Signature"

    def test_generic_header(self):
        assert GenericWebhookDriver.signature_header == "X-Webhook-Signature"


class WebhookSignatureIntegrationTests(TestCase):
    @patch.dict(
        os.environ,
        {"WEBHOOK_SECRET_GENERIC": "test-secret", "ENABLE_CELERY_ORCHESTRATION": "0"},
    )
    def test_valid_signature_accepted(self):
        payload = json.dumps({"name": "Test", "status": "firing"})
        body = payload.encode()
        sig = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
        assert response.status_code != 403

    @patch.dict(
        os.environ,
        {"WEBHOOK_SECRET_GENERIC": "test-secret", "ENABLE_CELERY_ORCHESTRATION": "0"},
    )
    def test_invalid_signature_rejected(self):
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE="invalid",
        )
        assert response.status_code == 403

    @patch.dict(
        os.environ,
        {"WEBHOOK_SECRET_GENERIC": "test-secret", "ENABLE_CELERY_ORCHESTRATION": "0"},
    )
    def test_missing_signature_header_rejected(self):
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
            # No signature header
        )
        assert response.status_code == 403

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_no_secret_configured_skips_verification(self):
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
        )
        assert response.status_code != 403

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_invalid_driver_name_skips_signature_check(self):
        """Invalid driver in URL triggers ValueError catch, skips sig check."""
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/nonexistent_driver/",
            data=payload,
            content_type="application/json",
        )
        assert response.status_code != 403

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_driver_without_signature_header_skips_check(self):
        """Alertmanager has no signature_header — verification is skipped."""
        payload = json.dumps(
            {
                "version": "4",
                "groupKey": "test",
                "receiver": "webhook",
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "Test", "severity": "warning"},
                        "annotations": {},
                        "startsAt": "2024-01-08T10:00:00Z",
                        "fingerprint": "fp1",
                    }
                ],
                "groupLabels": {},
                "commonLabels": {},
            }
        )

        client = Client()
        response = client.post(
            "/alerts/webhook/alertmanager/",
            data=payload,
            content_type="application/json",
        )
        assert response.status_code != 403
