from datetime import datetime
from unittest.mock import patch

from django.test import TestCase

from apps.alerts.drivers.opsgenie import OpsGenieDriver


class OpsGenieDriverValidateTests(TestCase):
    """Tests for OpsGenie driver validate()."""

    def setUp(self):
        self.driver = OpsGenieDriver()

    def test_validate_alert_with_alert_id(self):
        payload = {
            "action": "Create",
            "alert": {"alertId": "abc-123"},
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_alert_with_tiny_id(self):
        payload = {
            "action": "Create",
            "alert": {"tinyId": "42"},
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_integration_id_and_name(self):
        payload = {
            "integrationId": "int-1",
            "integrationName": "My Integration",
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_missing_required_fields(self):
        self.assertFalse(self.driver.validate({"random": "data"}))

    def test_validate_alert_without_alert_id_or_tiny_id(self):
        payload = {
            "action": "Create",
            "alert": {"message": "No IDs here"},
        }
        self.assertFalse(self.driver.validate(payload))


class OpsGenieDriverParseTests(TestCase):
    """Tests for OpsGenie driver parse()."""

    def setUp(self):
        self.driver = OpsGenieDriver()
        self.sample_payload = {
            "action": "Create",
            "alert": {
                "alertId": "abc-123",
                "message": "CPU high",
                "tags": ["env:prod", "team:infra", "urgent"],
                "tinyId": "42",
                "entity": "server-1",
                "alias": "cpu-high-server1",
                "createdAt": 1704708000000,
                "username": "admin",
                "description": "CPU usage above 90%",
                "team": "infra-team",
                "source": "monitoring",
                "priority": "P1",
            },
            "integrationId": "int-1",
            "integrationName": "Webhook",
        }

    def test_parse_valid_payload(self):
        result = self.driver.parse(self.sample_payload)

        self.assertEqual(result.source, "opsgenie")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "CPU high")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.fingerprint, "abc-123")
        self.assertEqual(alert.description, "CPU usage above 90%")

    def test_parse_invalid_payload_raises(self):
        with self.assertRaises(ValueError):
            self.driver.parse({"random": "data"})


class OpsGenieDriverParseAlertTests(TestCase):
    """Tests for OpsGenie driver _parse_alert()."""

    def setUp(self):
        self.driver = OpsGenieDriver()

    def _make_payload(self, **overrides):
        alert_overrides = overrides.pop("alert_overrides", {})
        alert = {
            "alertId": "abc-123",
            "message": "Test alert",
            "priority": "P3",
            "createdAt": 1704708000000,
        }
        alert.update(alert_overrides)
        payload = {
            "action": "Create",
            "alert": alert,
        }
        payload.update(overrides)
        return payload

    def test_resolved_action_close(self):
        payload = self._make_payload(action="Close")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "resolved")

    def test_resolved_action_acknowledge(self):
        payload = self._make_payload(action="Acknowledge")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "resolved")

    def test_resolved_action_ack(self):
        payload = self._make_payload(action="Ack")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "resolved")

    def test_resolved_action_resolve(self):
        payload = self._make_payload(action="Resolve")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "resolved")

    def test_resolved_action_delete(self):
        payload = self._make_payload(action="Delete")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "resolved")

    def test_firing_action(self):
        payload = self._make_payload(action="Create")
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "firing")

    def test_priority_p1_maps_to_critical(self):
        payload = self._make_payload(alert_overrides={"priority": "P1"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "critical")

    def test_priority_p2_maps_to_critical(self):
        payload = self._make_payload(alert_overrides={"priority": "P2"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "critical")

    def test_priority_p3_maps_to_warning(self):
        payload = self._make_payload(alert_overrides={"priority": "P3"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "warning")

    def test_priority_p4_maps_to_info(self):
        payload = self._make_payload(alert_overrides={"priority": "P4"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "info")

    def test_priority_p5_maps_to_info(self):
        payload = self._make_payload(alert_overrides={"priority": "P5"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "info")

    def test_unknown_priority_defaults_to_warning(self):
        payload = self._make_payload(alert_overrides={"priority": "P99"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "warning")

    def test_tags_key_value_pair(self):
        payload = self._make_payload(alert_overrides={"tags": ["env:prod"]})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["env"], "prod")

    def test_tags_plain_string(self):
        payload = self._make_payload(alert_overrides={"tags": ["urgent"]})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["tag_urgent"], "true")

    def test_tags_non_string_skipped(self):
        payload = self._make_payload(alert_overrides={"tags": [123, None]})
        alert = self.driver._parse_alert(payload)
        # Non-string tags should not create label entries
        self.assertNotIn("tag_123", alert.labels)
        self.assertNotIn("tag_None", alert.labels)

    def test_labels_entity_present(self):
        payload = self._make_payload(alert_overrides={"entity": "my-entity"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["entity"], "my-entity")

    def test_labels_entity_absent(self):
        payload = self._make_payload()
        alert = self.driver._parse_alert(payload)
        self.assertNotIn("entity", alert.labels)

    def test_labels_alias_present(self):
        payload = self._make_payload(alert_overrides={"alias": "my-alias"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["alias"], "my-alias")

    def test_labels_alias_absent(self):
        payload = self._make_payload()
        alert = self.driver._parse_alert(payload)
        self.assertNotIn("alias", alert.labels)

    def test_labels_team_present(self):
        payload = self._make_payload(alert_overrides={"team": "my-team"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["team"], "my-team")

    def test_labels_team_absent(self):
        payload = self._make_payload()
        alert = self.driver._parse_alert(payload)
        self.assertNotIn("team", alert.labels)

    def test_labels_source_present(self):
        payload = self._make_payload(alert_overrides={"source": "monitoring"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.labels["source"], "monitoring")

    def test_labels_source_absent(self):
        payload = self._make_payload()
        alert = self.driver._parse_alert(payload)
        self.assertNotIn("source", alert.labels)

    def test_fingerprint_from_alert_id(self):
        payload = self._make_payload(alert_overrides={"alertId": "id-1", "alias": "alias-1"})
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.fingerprint, "id-1")

    def test_fingerprint_fallback_to_alias(self):
        payload = self._make_payload(alert_overrides={"alertId": None, "alias": "alias-1"})
        # Remove alertId from the alert dict entirely for the falsy path
        payload["alert"].pop("alertId", None)
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.fingerprint, "alias-1")

    def test_fingerprint_fallback_to_generated(self):
        payload = self._make_payload()
        payload["alert"].pop("alertId", None)
        # No alias either
        alert = self.driver._parse_alert(payload)
        # Should be a generated hash fingerprint
        self.assertTrue(len(alert.fingerprint) > 0)
        self.assertNotEqual(alert.fingerprint, "")

    def test_annotations_contain_action_and_username(self):
        payload = self._make_payload(
            action="Create",
            alert_overrides={"username": "admin"},
        )
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.annotations["action"], "create")
        self.assertEqual(alert.annotations["username"], "admin")

    def test_default_message_when_missing(self):
        payload = self._make_payload()
        payload["alert"].pop("message", None)
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.name, "OpsGenie Alert")

    def test_default_description_when_missing(self):
        payload = self._make_payload()
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.description, "")

    def test_empty_tags_list(self):
        payload = self._make_payload(alert_overrides={"tags": []})
        alert = self.driver._parse_alert(payload)
        # Should still have alertname but no extra tag labels
        self.assertIn("alertname", alert.labels)

    def test_tags_not_a_list(self):
        payload = self._make_payload(alert_overrides={"tags": "not-a-list"})
        alert = self.driver._parse_alert(payload)
        # tags is truthy but not a list -- isinstance check fails
        self.assertIn("alertname", alert.labels)

    def test_missing_priority_defaults_to_p3(self):
        payload = self._make_payload()
        payload["alert"].pop("priority", None)
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.severity, "warning")
        self.assertEqual(alert.labels["priority"], "P3")

    def test_no_action_defaults_to_firing(self):
        payload = self._make_payload()
        payload.pop("action", None)
        alert = self.driver._parse_alert(payload)
        self.assertEqual(alert.status, "firing")


class OpsGenieDriverParseTimestampTests(TestCase):
    """Tests for OpsGenie driver _parse_timestamp()."""

    def setUp(self):
        self.driver = OpsGenieDriver()

    @patch("apps.alerts.drivers.opsgenie.datetime")
    def test_none_returns_now(self, mock_dt):
        now = datetime(2024, 1, 8, 10, 0, 0)
        mock_dt.now.return_value = now
        mock_dt.fromtimestamp = datetime.fromtimestamp

        result = self.driver._parse_timestamp(None)
        self.assertEqual(result, now)

    @patch("apps.alerts.drivers.opsgenie.datetime")
    def test_zero_returns_now(self, mock_dt):
        now = datetime(2024, 1, 8, 10, 0, 0)
        mock_dt.now.return_value = now
        mock_dt.fromtimestamp = datetime.fromtimestamp

        result = self.driver._parse_timestamp(0)
        self.assertEqual(result, now)

    def test_milliseconds_converted(self):
        # 1704708000000 ms = 1704708000 seconds
        result = self.driver._parse_timestamp(1704708000000)
        expected = datetime.fromtimestamp(1704708000)
        self.assertEqual(result, expected)

    def test_seconds_used_directly(self):
        result = self.driver._parse_timestamp(1704708000)
        expected = datetime.fromtimestamp(1704708000)
        self.assertEqual(result, expected)

    @patch("apps.alerts.drivers.opsgenie.datetime")
    def test_invalid_timestamp_returns_now(self, mock_dt):
        now = datetime(2024, 1, 8, 10, 0, 0)
        mock_dt.now.return_value = now
        mock_dt.fromtimestamp.side_effect = OSError("invalid")

        result = self.driver._parse_timestamp(99999999999999999999)
        self.assertEqual(result, now)
