from django.test import TestCase

from apps.orchestration.models import PipelineDefinition


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
            run_notify=False,
        )
        # A noisy critical hits the higher-priority exception first.
        self.assertEqual(resolve_pipeline({"source": "noisy", "severity": "critical"}), drop)

    def test_no_match_returns_none(self):
        from apps.orchestration.routing import resolve_pipeline

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


class FactsFromIncidentTests(TestCase):
    def test_pulls_source_labels_instance_from_alerts(self):
        from django.utils import timezone

        from apps.alerts.models import Alert, Incident
        from apps.orchestration.routing import facts_from_incident

        incident = Incident.objects.create(title="High CPU", severity="critical")
        Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
            labels={"instance_id": "web-03", "env": "prod"},
        )
        facts = facts_from_incident(incident)
        self.assertEqual(facts["source"], "cluster")
        self.assertEqual(facts["severity"], "critical")
        self.assertEqual(facts["instance"], "web-03")
        self.assertEqual(facts["labels"]["env"], "prod")


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

        return NotifyExecutor()._route_incident(ctx)

    def test_stamps_pipeline_and_returns_channel(self):
        from apps.notify.models import NotificationChannel

        incident = self._incident_with_alert()
        ch = NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
        )
        p = PipelineDefinition.objects.create(
            name="cluster-route",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
        )
        p.channels.add(ch)

        self.assertEqual(self._route(self._ctx(incident.id)), "ops-slack")
        incident.refresh_from_db()
        self.assertEqual(incident.pipeline_id, p.id)
        # Re-route is idempotent (already-stamped branch).
        self.assertEqual(self._route(self._ctx(incident.id)), "ops-slack")

    def test_no_incident_id(self):
        self.assertIsNone(self._route(self._ctx(None)))

    def test_incident_not_found(self):
        self.assertIsNone(self._route(self._ctx(999999)))

    def test_no_matching_pipeline(self):
        incident = self._incident_with_alert(source="grafana")
        PipelineDefinition.objects.create(
            name="only-cluster",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
        )
        self.assertIsNone(self._route(self._ctx(incident.id)))

    def test_matched_without_channels_still_stamps_but_returns_none(self):
        incident = self._incident_with_alert()
        p = PipelineDefinition.objects.create(
            name="cluster-no-ch",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
        )
        self.assertIsNone(self._route(self._ctx(incident.id)))
        incident.refresh_from_db()
        self.assertEqual(incident.pipeline_id, p.id)  # stamped even without a channel
