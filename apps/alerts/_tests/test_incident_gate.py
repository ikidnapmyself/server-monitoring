"""Tests for the incident gate: one assertion per row of the design doc §2 table."""

from django.test import SimpleTestCase

from apps.alerts.incident_gate import follow_alert
from apps.alerts.models import Incident, IncidentStatus


def _inc(status):
    return Incident(status=status, severity="warning")


class FollowAlertTests(SimpleTestCase):
    def test_open_any_material_change_notifies_without_reopen(self):
        self.assertEqual(
            follow_alert(_inc(IncidentStatus.OPEN), "warning", "warning", "firing", "firing"),
            (False, True),
        )

    def test_ack_escalation_reopens_and_notifies(self):
        self.assertEqual(
            follow_alert(
                _inc(IncidentStatus.ACKNOWLEDGED), "warning", "critical", "firing", "firing"
            ),
            (True, True),
        )

    def test_ack_refire_is_absorbed(self):
        self.assertEqual(
            follow_alert(
                _inc(IncidentStatus.ACKNOWLEDGED), "warning", "warning", "resolved", "firing"
            ),
            (False, False),
        )

    def test_ack_alert_resolving_still_sends_the_all_clear(self):
        self.assertEqual(
            follow_alert(
                _inc(IncidentStatus.ACKNOWLEDGED), "warning", "warning", "firing", "resolved"
            ),
            (False, True),
        )

    def test_ack_deescalation_is_absorbed(self):
        self.assertEqual(
            follow_alert(
                _inc(IncidentStatus.ACKNOWLEDGED), "critical", "warning", "firing", "firing"
            ),
            (False, False),
        )

    def test_resolved_refire_reopens_and_notifies(self):
        self.assertEqual(
            follow_alert(_inc(IncidentStatus.RESOLVED), "warning", "warning", "resolved", "firing"),
            (True, True),
        )

    def test_closed_severity_change_while_firing_reopens_and_notifies(self):
        self.assertEqual(
            follow_alert(_inc(IncidentStatus.CLOSED), "warning", "critical", "firing", "firing"),
            (True, True),
        )

    def test_resolved_alert_resolving_is_absorbed(self):
        self.assertEqual(
            follow_alert(_inc(IncidentStatus.RESOLVED), "warning", "warning", "firing", "resolved"),
            (False, False),
        )

    def test_no_incident_notifies_nothing(self):
        self.assertEqual(
            follow_alert(None, "warning", "critical", "firing", "firing"),
            (False, False),
        )
