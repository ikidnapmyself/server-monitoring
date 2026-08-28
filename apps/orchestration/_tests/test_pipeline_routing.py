from django.test import TestCase

from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes


class PipelineMatchesTests(TestCase):
    def _p(self, match):
        return PipelineDefinition(name="p", match=match)

    def test_empty_match_is_catch_all(self):
        self.assertTrue(self._p([]).matches({"source": "anything"}))

    def test_is(self):
        p = self._p([{"field": "source", "op": "is", "value": "grafana"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "cluster"}))

    def test_is_not(self):
        p = self._p([{"field": "source", "op": "is-not", "value": "noisy"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "noisy"}))

    def test_in(self):
        p = self._p([{"field": "severity", "op": "in", "value": ["critical", "warning"]}])
        self.assertTrue(p.matches({"severity": "critical"}))
        self.assertFalse(p.matches({"severity": "info"}))

    def test_not_in(self):
        p = self._p([{"field": "severity", "op": "not-in", "value": ["info"]}])
        self.assertTrue(p.matches({"severity": "critical"}))
        self.assertFalse(p.matches({"severity": "info"}))

    def test_label_lookup(self):
        p = self._p([{"field": "label:env", "op": "is", "value": "prod"}])
        self.assertTrue(p.matches({"labels": {"env": "prod"}}))
        self.assertFalse(p.matches({"labels": {"env": "dev"}}))
        self.assertFalse(p.matches({"labels": {}}))

    def test_multiple_conditions_are_anded(self):
        p = self._p(
            [
                {"field": "source", "op": "is", "value": "cluster"},
                {"field": "severity", "op": "in", "value": ["critical"]},
            ]
        )
        self.assertTrue(p.matches({"source": "cluster", "severity": "critical"}))
        self.assertFalse(p.matches({"source": "cluster", "severity": "info"}))

    def test_default_op_is_equality(self):
        # op omitted defaults to "is".
        p = self._p([{"field": "source", "value": "grafana"}])
        self.assertTrue(p.matches({"source": "grafana"}))
        self.assertFalse(p.matches({"source": "other"}))

    def test_unknown_op_fails_closed(self):
        # A typoed/unknown op must NOT match everything.
        p = self._p([{"field": "source", "op": "equals", "value": "grafana"}])
        self.assertFalse(p.matches({"source": "grafana"}))

    def test_non_dict_condition_fails_closed(self):
        p = self._p(["not-a-dict"])
        self.assertFalse(p.matches({"source": "grafana"}))

    def test_in_with_non_list_value_fails_closed(self):
        p = self._p([{"field": "severity", "op": "in", "value": "critical"}])
        self.assertFalse(p.matches({"severity": "critical"}))

    def test_not_in_with_non_list_value_fails_closed(self):
        p = self._p([{"field": "severity", "op": "not-in", "value": "info"}])
        self.assertFalse(p.matches({"severity": "critical"}))


class ResolvePipelineTests(TestCase):
    def test_first_match_wins_by_priority(self):
        from apps.orchestration.routing import resolve_pipeline

        PipelineDefinition.objects.create(
            name="general", priority=100, match=[]
        )  # catch-all, lower priority
        specific = PipelineDefinition.objects.create(
            name="grafana",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "grafana"}],
        )
        self.assertEqual(resolve_pipeline({"source": "grafana"}), specific)

    def test_exception_via_higher_priority_rule(self):
        from apps.orchestration.routing import resolve_pipeline

        PipelineDefinition.objects.create(
            name="all-critical",
            priority=50,
            match=[{"field": "severity", "op": "is", "value": "critical"}],
        )
        drop = PipelineDefinition.objects.create(
            name="noisy-exception",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "noisy"}],
            stages=["check", "analyze"],
        )
        # A noisy critical hits the higher-priority exception first.
        self.assertEqual(resolve_pipeline({"source": "noisy", "severity": "critical"}), drop)

    def test_no_match_returns_none(self):
        from apps.orchestration.routing import resolve_pipeline

        # Migration 0012 seeds a catch-all, so "nothing matches" only exists once
        # an operator has removed it. That is the state under test here.
        clear_lanes()
        PipelineDefinition.objects.create(
            name="only-cluster",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
        )
        self.assertIsNone(resolve_pipeline({"source": "grafana"}))

    def test_inactive_pipelines_skipped(self):
        from apps.orchestration.routing import resolve_pipeline

        PipelineDefinition.objects.create(name="off", priority=1, match=[], is_active=False)
        active = PipelineDefinition.objects.create(name="on", priority=100, match=[])
        self.assertEqual(resolve_pipeline({"source": "x"}), active)


class FactsFromAlertTests(TestCase):
    """Facts come from ONE alert — never merged across an incident's alerts."""

    def _alert(self, **kwargs):
        from django.utils import timezone

        from apps.alerts.models import Alert

        defaults = {
            "fingerprint": "fp",
            "source": "cluster",
            "name": "cpu",
            "severity": "critical",
            "started_at": timezone.now(),
            "labels": {"instance_id": "web-03", "env": "prod"},
        }
        defaults.update(kwargs)
        return Alert.objects.create(**defaults)

    def test_pulls_source_severity_instance_labels_from_the_alert(self):
        from apps.orchestration.routing import facts_from_alert

        facts = facts_from_alert(self._alert(), origin="incoming_webhook")
        self.assertEqual(facts["source"], "cluster")
        self.assertEqual(facts["severity"], "critical")
        self.assertEqual(facts["instance"], "web-03")
        self.assertEqual(facts["labels"]["env"], "prod")
        self.assertEqual(facts["origin"], "incoming_webhook")

    def test_severity_is_the_alerts_own_not_the_incidents(self):
        """An incident's severity is the max across its alerts; a warning alert
        on a critical incident must route as ``warning``."""
        from apps.alerts.models import Incident
        from apps.orchestration.routing import facts_from_alert

        incident = Incident.objects.create(title="High CPU", severity="critical")
        alert = self._alert(severity="warning", incident=incident)
        self.assertEqual(facts_from_alert(alert, "incoming_webhook")["severity"], "warning")

    def test_origin_is_passed_through_verbatim(self):
        """No default: an omitted origin would silently satisfy every is-not lane."""
        from apps.orchestration.routing import facts_from_alert

        self.assertEqual(facts_from_alert(self._alert(), "manual")["origin"], "manual")

    def test_blank_source_and_severity_normalise_to_empty_string(self):
        from apps.orchestration.routing import facts_from_alert

        facts = facts_from_alert(self._alert(source="", severity=""), "incoming_webhook")
        self.assertEqual(facts["source"], "")
        self.assertEqual(facts["severity"], "")

    def test_instance_falls_through_instance_id_instance_hostname(self):
        from apps.orchestration.routing import facts_from_alert

        cases = [
            ({"instance_id": "a", "instance": "b", "hostname": "c"}, "a"),
            ({"instance": "b", "hostname": "c"}, "b"),
            ({"hostname": "c"}, "c"),
            ({}, ""),
        ]
        for i, (labels, expected) in enumerate(cases):
            with self.subTest(labels=labels):
                alert = self._alert(fingerprint=f"fp-{i}", labels=labels)
                self.assertEqual(facts_from_alert(alert, "incoming_webhook")["instance"], expected)

    def test_status_is_a_routing_fact(self):
        """A lane cannot match on a fact that is not produced.

        The seeded ``resolved-all-clear`` lane routes on it: an all-clear has
        nothing left to diagnose, so it notifies without an LLM call.
        """
        from apps.orchestration.routing import facts_from_alert

        firing = facts_from_alert(self._alert(status="firing"), "incoming_webhook")
        resolved = facts_from_alert(
            self._alert(fingerprint="fp-res", status="resolved"), "incoming_webhook"
        )
        self.assertEqual(firing["status"], "firing")
        self.assertEqual(resolved["status"], "resolved")

    def test_blank_status_normalises_to_empty_string(self):
        from apps.orchestration.routing import facts_from_alert

        self.assertEqual(facts_from_alert(self._alert(status=""), "incoming_webhook")["status"], "")

    def test_non_dict_labels_yield_empty_labels_and_instance(self):
        from apps.orchestration.routing import facts_from_alert

        facts = facts_from_alert(self._alert(labels="pwned"), "incoming_webhook")
        self.assertEqual(facts["labels"], {})
        self.assertEqual(facts["instance"], "")


class OriginMatchingTests(TestCase):
    """``origin`` is a first-class routing fact — lanes can match the entry point."""

    def _lane(self, origin_value):
        return PipelineDefinition(
            name="p", match=[{"field": "origin", "op": "is", "value": origin_value}]
        )

    def test_lane_matches_its_origin_and_not_another(self):
        lane = self._lane("checker_generated")
        self.assertTrue(lane.matches({"origin": "checker_generated"}))
        self.assertFalse(lane.matches({"origin": "incoming_webhook"}))
        self.assertFalse(lane.matches({"origin": ""}))

    def test_resolve_pipeline_picks_the_lane_for_this_origin(self):
        from apps.orchestration.routing import resolve_pipeline

        # Without this, the seeded catch-all claims the "manual" case below.
        clear_lanes()
        webhook = PipelineDefinition.objects.create(
            name="webhook-lane",
            priority=10,
            match=[{"field": "origin", "op": "is", "value": "incoming_webhook"}],
        )
        checker = PipelineDefinition.objects.create(
            name="checker-lane",
            priority=20,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
        )
        self.assertEqual(resolve_pipeline({"origin": "incoming_webhook"}), webhook)
        self.assertEqual(resolve_pipeline({"origin": "checker_generated"}), checker)
        self.assertIsNone(resolve_pipeline({"origin": "manual"}))


class RouteIncidentTests(TestCase):
    def _ctx(self, incident_id):
        from apps.orchestration.dtos import StageContext

        return StageContext(
            trace_id="t",
            run_id="r",
            incident_id=incident_id,
            payload={},
            previous_results={},
            source="cluster",
        )

    def _incident_with_alert(self, source="cluster", severity="critical"):
        from django.utils import timezone

        from apps.alerts.models import Alert, Incident

        incident = Incident.objects.create(title="x", severity=severity)
        Alert.objects.create(
            fingerprint=f"fp-{source}",
            source=source,
            name="cpu",
            severity=severity,
            started_at=timezone.now(),
            incident=incident,
            labels={"instance_id": "web"},
        )
        return incident

    def _route(self, ctx):
        from apps.orchestration.executors import NotifyExecutor

        return NotifyExecutor()._route_incident(NotifyExecutor()._load_incident(ctx.incident_id))

    def _stamp(self, incident, **defn_kwargs):
        p = PipelineDefinition.objects.create(priority=10, match=[], **defn_kwargs)
        incident.pipeline = p
        incident.save(update_fields=["pipeline"])
        return p

    def test_returns_channel_of_stamped_pipeline(self):
        # Phase B: the pipeline is stamped on the incident before notify runs;
        # _route_incident just reads it and returns its single channel.
        from apps.notify.models import NotificationChannel

        incident = self._incident_with_alert()
        ch = NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
        )
        self._stamp(incident, name="cluster-route", channel=ch)

        self.assertEqual(self._route(self._ctx(incident.id)), "ops-slack")

    def test_inactive_channel_returns_none(self):
        """An inactive channel routes nowhere; the caller falls back to the payload."""
        from apps.notify.models import NotificationChannel

        incident = self._incident_with_alert()
        ch = NotificationChannel.objects.create(
            name="off-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
            is_active=False,
        )
        self._stamp(incident, name="inactive-route", channel=ch)

        self.assertIsNone(self._route(self._ctx(incident.id)))

    def test_no_incident_id(self):
        self.assertIsNone(self._route(self._ctx(None)))

    def test_incident_not_found(self):
        self.assertIsNone(self._route(self._ctx(999999)))

    def test_no_stamped_pipeline_returns_none(self):
        incident = self._incident_with_alert()  # incident.pipeline is None
        self.assertIsNone(self._route(self._ctx(incident.id)))

    def test_stamped_pipeline_without_channel_returns_none(self):
        incident = self._incident_with_alert()
        self._stamp(incident, name="no-ch")
        self.assertIsNone(self._route(self._ctx(incident.id)))


class HubLocalRunRoutesLikeANodeTests(TestCase):
    """The hub's own checks take the node lane, against the SEEDED routing table.

    A hub-local check run carries both facts at once: ``source`` is ``cluster``
    (checker alerts share one identity whether the machine pushed them or ran them
    here) and ``origin`` is ``checker_generated``. It must reach the lane that
    notifies, so the hub can page about its own full disk exactly as it pages about
    any other node's. Nothing is cleared here on purpose — the seeded rows are what
    a fresh install routes on, and a second lane at priority 50 matching the same
    run would be settled by nothing better than id order.
    """

    def test_hub_local_run_routes_to_the_node_lane(self):
        from apps.orchestration.routing import resolve_pipeline

        facts = {
            "source": "cluster",
            "severity": "critical",
            "status": "firing",
            "instance": "hub-01",
            "labels": {"instance_id": "hub-01"},
            "origin": "checker_generated",
        }

        self.assertEqual(resolve_pipeline(facts).name, "cluster-nodes")

    def test_no_other_seeded_lane_ties_with_the_node_lane(self):
        """The win must come from the table, not from which row was inserted first."""
        node = PipelineDefinition.objects.get(name="cluster-nodes")
        rivals = PipelineDefinition.objects.filter(is_active=True, priority=node.priority).exclude(
            pk=node.pk
        )

        self.assertEqual(list(rivals), [])
