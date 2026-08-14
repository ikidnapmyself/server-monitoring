"""Phase B: stage selection comes from the matched pipeline's ``stages`` list.

INGEST always runs; the downstream stages (check/analyze/notify) are the ones the
resolved PipelineDefinition lists in ``stages``, in that order, and the pipeline is
stamped on the incident right after ingest. ``checks_only`` stays as a CLI
invocation flag; there is no longer any payload or driver flag that edits a
matched lane's stage list.

Since Task 6 there is no implicit fallback: unmatched traffic fails as a
non-retryable ``no_route``, and the routes that used to be hard-coded live in the
rows migration ``0012`` seeds (``cluster-nodes``, ``catch-all``). Those rows are
present in the test database exactly as they are on a fresh install, so tests
that need "nothing matches" delete them first.
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.orchestration import routing
from apps.orchestration.dtos import AnalyzeResult, CheckResult, IngestResult, NotifyResult
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineOrigin,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.orchestrator import PipelineOrchestrator
from apps.orchestration.testing import clear_lanes


class StageSelectionFromStagesListTests(TestCase):
    def setUp(self):
        self.incident = Incident.objects.create(title="High CPU", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-cluster",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "web-03"},
        )

    def _fake_exec(self, pipeline_run, stage, payload, previous_results, incident_id):
        return {
            PipelineStage.INGEST: IngestResult(
                alert_id=self.alert.id, incident_id=self.incident.id, alerts_created=1
            ),
            PipelineStage.CHECK: CheckResult(checks_run=1),
            PipelineStage.ANALYZE: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: NotifyResult(channels_succeeded=1),
        }[stage]

    def _run(self, payload=None, origin=None):
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            return PipelineOrchestrator().run_pipeline(
                payload=payload or {"payload": {}}, source="cluster", origin=origin
            )

    def test_run_origin_reaches_routing_and_selects_the_lane(self):
        """The run's origin is a routing fact, not just a stored column.

        Not end to end for checker_generated: production only sets that origin on
        checks_only runs, which take the CHECK-only branch and never reach routing,
        so no such lane can fire yet. Task 8 closes that gap. The origin plumbing
        itself is what this pins, and it is live for incoming_webhook and manual.
        """
        PipelineDefinition.objects.create(
            name="checker-lane",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["notify"],
        )
        result = self._run(origin=PipelineOrigin.CHECKER_GENERATED)
        assert result.stages_completed == [PipelineStage.INGEST, PipelineStage.NOTIFY]

        # The same payload from a webhook does not match that lane; it falls
        # through to the seeded cluster-nodes lane instead.
        webhook = self._run(origin=PipelineOrigin.INCOMING_WEBHOOK)
        assert webhook.stages_completed == [
            PipelineStage.INGEST,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert Incident.objects.get(pk=self.incident.pk).pipeline.name == "cluster-nodes"

    def test_subject_alert_from_the_ingest_result_is_what_routes(self):
        """The lane matches the subject alert's labels, proving alert_id is used."""
        lane = PipelineDefinition.objects.create(
            name="web-03-lane",
            priority=1,
            match=[{"field": "instance", "op": "is", "value": "web-03"}],
            stages=["notify"],
        )
        result = self._run()
        assert result.stages_completed == [PipelineStage.INGEST, PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=self.incident.pk).pipeline_id == lane.id

    def test_stages_without_check_skips_check_stage(self):
        PipelineDefinition.objects.create(
            name="no-check", match=[], priority=1, stages=["analyze", "notify"]
        )
        result = self._run()
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_stages_without_analyze_skips_analyze_stage(self):
        PipelineDefinition.objects.create(
            name="no-ai", match=[], priority=1, stages=["check", "notify"]
        )
        result = self._run()
        assert PipelineStage.ANALYZE not in result.stages_completed
        assert PipelineStage.CHECK in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_stages_without_notify_stops_before_notify(self):
        PipelineDefinition.objects.create(
            name="silent", match=[], priority=1, stages=["check", "analyze"]
        )
        result = self._run()
        assert PipelineStage.NOTIFY not in result.stages_completed

    def test_empty_stages_runs_only_ingest(self):
        PipelineDefinition.objects.create(
            name="inbox-lite",
            match=[],
            priority=1,
            stages=[],
        )
        result = self._run()
        assert result.stages_completed == [PipelineStage.INGEST]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.INGESTED

    def test_no_matching_operator_lane_falls_through_to_the_seeded_lanes(self):
        """Was ``test_no_matching_pipeline_runs_full_order``.

        That name described the deleted Python fallback. The observable behaviour
        is now a seeded row, so the assertion moves with it: a cluster alert no
        operator lane claims lands on ``cluster-nodes``, which omits CHECK.
        """
        PipelineDefinition.objects.create(
            name="grafana-only",
            match=[{"field": "source", "op": "is", "value": "grafana"}],
            priority=1,
        )
        result = self._run()
        assert result.stages_completed == [
            PipelineStage.INGEST,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]

    def test_no_operator_lanes_at_all_still_routes_on_the_seeds(self):
        """Was ``test_no_pipelines_at_all_runs_full_order`` — same reason as above.

        A fresh install with nothing configured still routes, because the routes
        are rows now rather than a constant in the orchestrator.
        """
        result = self._run()
        assert result.stages_completed == [
            PipelineStage.INGEST,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]

    def test_incident_stamped_with_matched_pipeline(self):
        p = PipelineDefinition.objects.create(name="stamp-lane", match=[], priority=1)
        self._run()
        self.incident.refresh_from_db()
        assert self.incident.pipeline_id == p.id

    def test_a_lane_listing_check_runs_check_for_cluster_traffic_too(self):
        """Was ``test_skip_checkers_payload_override_still_wins`` — inverted.

        A payload flag used to strip CHECK from a lane that listed it, so cluster
        traffic could never be checked whatever the table said. Nothing overrides
        a lane now: the list an operator saved is the list that runs, even when
        the result (hub-side checks on a node's alert) is useless. That is the
        accepted cost of holding the rule as data only.
        """
        PipelineDefinition.objects.create(
            name="full", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        result = self._run()
        assert PipelineStage.CHECK in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_checks_only_payload_override_runs_only_check(self):
        PipelineDefinition.objects.create(name="full", match=[], priority=1)
        result = self._run(payload={"payload": {}, "checks_only": True})
        assert result.stages_completed == [PipelineStage.CHECK]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.CHECKED


class DownstreamStagesHelperTests(TestCase):
    """Direct unit tests for _downstream_stages edge cases."""

    def _alert(self, source="cluster", labels=None, incident=None):
        return Alert.objects.create(
            fingerprint=f"fp-{source}",
            source=source,
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
            labels=labels or {},
        )

    def _incident_alert(self, source="cluster"):
        incident = Incident.objects.create(title="x", severity="critical")
        return self._alert(source=source, incident=incident), incident

    def _downstream(self, alert_id, origin="incoming_webhook"):
        return PipelineOrchestrator()._downstream_stages(alert_id, origin)

    def test_junk_stages_value_degrades_instead_of_failing_the_run(self):
        """A hand-edited lane must not turn every run into an endless retryable failure.

        ``PipelineStage("sparkle")`` raises ValueError, which the orchestrator's generic
        handler would turn into FAILED/retryable=True *after* INGEST already succeeded.
        Normalising on the model degrades to the valid subset instead.
        """
        alert, _ = self._incident_alert()
        PipelineDefinition.objects.create(
            name="junk", match=[], priority=1, stages=["sparkle", "notify"]
        )
        with self.assertLogs("apps.orchestration.orchestrator", level="WARNING") as logs:
            stages = self._downstream(alert.id)
        assert stages == [PipelineStage.NOTIFY]
        # The warning names the lane so an operator can find the bad row.
        assert "junk" in logs.output[0]

    def test_valid_lane_logs_no_warning(self):
        alert, _ = self._incident_alert()
        PipelineDefinition.objects.create(
            name="clean", match=[], priority=1, stages=["check", "notify"]
        )
        with patch("apps.orchestration.orchestrator.logger") as mock_logger:
            stages = self._downstream(alert.id)
        assert stages == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        mock_logger.warning.assert_not_called()

    def test_matched_lane_stages_arrive_in_listed_order(self):
        """The list an operator saved is the list the orchestrator executes."""
        alert, _ = self._incident_alert()
        PipelineDefinition.objects.create(
            name="lane-full", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        assert self._downstream(alert.id) == [
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]

    def test_no_alert_id_returns_empty_list(self):
        """Nothing to route is not an error — and not the default lane either."""
        assert self._downstream(None) == []

    def test_missing_alert_row_returns_empty_list(self):
        assert self._downstream(999999) == []

    def test_no_matching_lane_returns_none(self):
        """``None`` means "an alert exists but nothing routed it" — distinct from ``[]``."""
        alert, _ = self._incident_alert()
        # The seeded lanes would claim this alert; the no-route state only exists
        # on a database where an operator has removed them.
        clear_lanes()
        PipelineDefinition.objects.create(
            name="grafana-only",
            priority=1,
            match=[{"field": "source", "op": "is", "value": "grafana"}],
        )
        assert self._downstream(alert.id) is None

    # The three ``_downstream_stages_or_default`` tests that stood here are gone with
    # the method. Their surviving cases are covered by ``test_matched_lane_stages_
    # arrive_in_listed_order`` (pass-through), ``test_no_alert_id_returns_empty_list``
    # ([] stays []), and ``NoRouteFailsTheRunTests`` (what a no-match does now).

    def test_alert_without_an_incident_still_routes(self):
        """Routing needs the alert, not the incident; the stamp is just skipped."""
        alert = self._alert(source="cluster")
        PipelineDefinition.objects.create(name="lane", match=[], priority=1, stages=["notify"])
        assert self._downstream(alert.id) == [PipelineStage.NOTIFY]

    def test_lane_matching_on_origin_selects_the_stages(self):
        alert, incident = self._incident_alert()
        checker_lane = PipelineDefinition.objects.create(
            name="checker-lane",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["notify"],
        )
        assert self._downstream(alert.id, origin="checker_generated") == [PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=incident.pk).pipeline_id == checker_lane.id
        # A different origin does not match that lane at all: it falls through to
        # the seeded cluster-nodes lane, which lists different stages.
        assert self._downstream(alert.id, origin="incoming_webhook") == [
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert Incident.objects.get(pk=incident.pk).pipeline.name == "cluster-nodes"

    def test_routes_on_the_subject_alerts_own_facts_not_a_merge(self):
        """Regression: facts must come from ONE alert.

        The old ``facts_from_incident`` merged every alert on the incident — labels
        from the OLDEST (later ``update()`` calls won) and source from the NEWEST.
        Here the older alert carries ``env=prod`` and the subject alert carries
        ``env=staging``; the merge would have picked the decoy lane.
        """
        incident = Incident.objects.create(title="x", severity="critical")
        self._alert(
            source="grafana", labels={"env": "prod", "hostname": "web-01"}, incident=incident
        )
        subject = self._alert(
            source="cluster", labels={"env": "staging", "hostname": "web-02"}, incident=incident
        )
        subject_lane = PipelineDefinition.objects.create(
            name="subject-lane",
            priority=1,
            match=[
                {"field": "source", "op": "is", "value": "cluster"},
                {"field": "instance", "op": "is", "value": "web-02"},
                {"field": "label:env", "op": "is", "value": "staging"},
            ],
            stages=["notify"],
        )
        PipelineDefinition.objects.create(
            name="merged-facts-decoy",
            priority=2,
            match=[{"field": "label:env", "op": "is", "value": "prod"}],
            stages=["check", "analyze"],
        )

        assert self._downstream(subject.id) == [PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=incident.pk).pipeline_id == subject_lane.id

    def test_the_incident_rides_the_alert_query(self):
        """``alert.incident`` is read on every matched run, so it must be joined.

        Asserts the join on the alert the orchestrator itself loaded, rather than a
        query count — a count would also fail, misleadingly naming select_related,
        if resolve_pipeline ever added a query of its own.

        The snapshot is taken inside the facts_from_alert call, which runs *before*
        ``_downstream_stages`` touches ``alert.incident``: that access populates
        fields_cache lazily, so checking afterwards would pass either way.
        """
        alert, _ = self._incident_alert()
        PipelineDefinition.objects.create(name="ca", match=[], priority=1, stages=["notify"])
        joined = []
        real_facts = routing.facts_from_alert

        def spy(a, origin):
            joined.append("incident" in a._state.fields_cache)
            return real_facts(a, origin)

        with patch.object(routing, "facts_from_alert", spy):
            assert self._downstream(alert.id) == [PipelineStage.NOTIFY]

        assert joined == [True]

    def test_already_stamped_pipeline_is_not_re_saved(self):
        alert, incident = self._incident_alert()
        p = PipelineDefinition.objects.create(
            name="ca", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        incident.pipeline = p
        incident.save(update_fields=["pipeline"])
        before = Incident.objects.get(pk=incident.pk).updated_at
        stages = self._downstream(alert.id)
        reloaded = Incident.objects.get(pk=incident.pk)
        assert reloaded.pipeline_id == p.id
        assert reloaded.updated_at == before  # idempotent: no redundant save
        assert stages == [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]


class ResumeRoutesOnTheSnapshotAlertTests(TestCase):
    """A resumed run re-routes from the alert_id stored in the INGEST snapshot."""

    def setUp(self):
        self.incident = Incident.objects.create(title="High CPU", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-resume",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "web-09"},
        )

    def _fake_exec(self, pipeline_run, stage, payload, previous_results, incident_id):
        return {
            PipelineStage.CHECK: CheckResult(checks_run=1),
            PipelineStage.ANALYZE: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: NotifyResult(channels_succeeded=1),
        }[stage]

    def test_resume_reads_alert_id_from_the_ingest_snapshot(self):
        lane = PipelineDefinition.objects.create(
            name="web-09-lane",
            priority=1,
            match=[{"field": "instance", "op": "is", "value": "web-09"}],
            stages=["notify"],
        )
        run = PipelineRun.objects.create(
            trace_id="t-resume",
            run_id="r-resume",
            source="cluster",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.INCOMING_WEBHOOK,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.INGEST,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"alert_id": self.alert.id, "incident_id": self.incident.id},
        )
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            result = PipelineOrchestrator().resume_pipeline(run_id="r-resume", payload={})

        assert result.stages_completed == [PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=self.incident.pk).pipeline_id == lane.id


class LegacySnapshotResumeTests(TestCase):
    """A run whose INGEST snapshot predates ``alert_id`` must still drain.

    33 such runs existed on the dev database when this was written — all FAILED,
    all resumable via the resume endpoint or the admin's "Mark for Retry". Without
    the fallback they resume to a COMPLETED run that executed nothing, leaving the
    incident unstamped and the diagnosis strip reporting ``never_ran``.
    """

    def setUp(self):
        self.incident = Incident.objects.create(title="High CPU", severity="critical")
        self.warning = Alert.objects.create(
            fingerprint="fp-warn",
            source="cluster",
            name="cpu",
            severity="warning",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "web-07"},
        )
        self.critical = Alert.objects.create(
            fingerprint="fp-crit",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "web-07"},
        )

    def _fake_exec(self, pipeline_run, stage, payload, previous_results, incident_id):
        return {
            PipelineStage.CHECK: CheckResult(checks_run=1),
            PipelineStage.ANALYZE: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: NotifyResult(channels_succeeded=1),
        }[stage]

    def _legacy_run(self, snapshot):
        run = PipelineRun.objects.create(
            trace_id="t-legacy",
            run_id="r-legacy",
            source="cluster",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.INCOMING_WEBHOOK,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.INGEST,
            status=StageStatus.SUCCEEDED,
            output_snapshot=snapshot,
        )
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            return PipelineOrchestrator().resume_pipeline(run_id="r-legacy", payload={})

    def test_snapshot_without_alert_id_still_routes_and_drains(self):
        lane = PipelineDefinition.objects.create(
            name="web-07-lane",
            priority=1,
            match=[{"field": "instance", "op": "is", "value": "web-07"}],
            stages=["check", "notify"],
        )
        result = self._legacy_run({"incident_id": self.incident.id, "severity": "critical"})

        assert result.stages_completed == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        # The incident is stamped, so the diagnosis strip agrees with what ran.
        assert Incident.objects.get(pk=self.incident.pk).pipeline_id == lane.id

    def test_legacy_subject_is_the_most_severe_alert(self):
        """Same selection rule as ingest, so the lane sees the severity ingest saw."""
        PipelineDefinition.objects.create(
            name="critical-only",
            priority=1,
            match=[{"field": "severity", "op": "is", "value": "critical"}],
            stages=["notify"],
        )
        result = self._legacy_run({"incident_id": self.incident.id})
        assert result.stages_completed == [PipelineStage.NOTIFY]
        assert PipelineOrchestrator()._legacy_subject_alert_id(self.incident.id) == self.critical.id

    def test_snapshot_with_neither_id_still_stops_cleanly(self):
        PipelineDefinition.objects.create(name="ca", match=[], priority=1, stages=["notify"])
        result = self._legacy_run({"severity": "critical"})
        assert result.stages_completed == []

    def test_incident_with_no_alerts_yields_no_subject(self):
        empty = Incident.objects.create(title="ghost", severity="info")
        assert PipelineOrchestrator()._legacy_subject_alert_id(empty.id) is None


class SeededDefaultLanesTests(TestCase):
    """The lanes migration ``0012`` seeds are ordinary rows, and they route.

    They exist in the test database because migrations created them, which is
    exactly how a fresh install sees them. Nothing here mocks routing: the
    assertions go through ``_downstream_stages`` against the real rows.
    """

    def _alert(self, source, labels=None):
        incident = Incident.objects.create(title="x", severity="critical")
        return Alert.objects.create(
            fingerprint=f"fp-seed-{source}",
            source=source,
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
            labels=labels or {},
        )

    def _downstream(self, alert_id, origin="incoming_webhook"):
        return PipelineOrchestrator()._downstream_stages(alert_id, origin)

    def test_both_seeded_lanes_exist_and_are_active(self):
        seeded = {
            d.name: d
            for d in PipelineDefinition.objects.filter(name__in=["cluster-nodes", "catch-all"])
        }
        assert set(seeded) == {"cluster-nodes", "catch-all"}
        assert seeded["cluster-nodes"].priority == 50
        assert seeded["catch-all"].priority == 1000
        assert all(d.is_active for d in seeded.values())

    def test_cluster_nodes_lane_matches_a_cluster_alert_and_omits_check(self):
        """A node already ran its own checkers; hub-side CHECK would measure the hub."""
        alert = self._alert("cluster")
        assert self._downstream(alert.id) == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=alert.incident_id).pipeline.name == "cluster-nodes"

    def test_catch_all_lane_matches_when_nothing_else_does(self):
        alert = self._alert("grafana")
        assert self._downstream(alert.id) == [
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert Incident.objects.get(pk=alert.incident_id).pipeline.name == "catch-all"

    def test_cluster_traffic_prefers_cluster_nodes_over_the_catch_all(self):
        """Priority ordering, asserted with both seeded lanes present and active.

        Recreating ``cluster-nodes`` gives it the *higher* id, so it can no longer
        win the ``(priority, id)`` sort on insertion order. If 0012 ever seeds the
        two lanes at the same priority, this test fails; asserting on the routing
        outcome alone would have passed on id luck.
        """
        cluster = PipelineDefinition.objects.get(name="cluster-nodes")
        catch_all = PipelineDefinition.objects.get(name="catch-all")
        fields = {
            f.name: getattr(cluster, f.name)
            for f in PipelineDefinition._meta.fields
            if f.name not in ("id", "created_at", "updated_at")
        }
        cluster.delete()
        cluster = PipelineDefinition.objects.create(**fields)
        assert cluster.id > catch_all.id
        assert cluster.is_active and catch_all.is_active

        # The seeded lane must also beat a hand-created lane, which takes the
        # model's default priority — read it rather than restating 100 here.
        assert cluster.priority < PipelineDefinition._meta.get_field("priority").default

        alert = self._alert("cluster")
        assert PipelineStage.CHECK not in self._downstream(alert.id)
        assert Incident.objects.get(pk=alert.incident_id).pipeline_id == cluster.id

    def test_seeded_lanes_are_deletable_like_any_operator_row(self):
        """They are rows, not special cases: removing them removes the behaviour."""
        PipelineDefinition.objects.filter(name__in=["cluster-nodes", "catch-all"]).delete()
        alert = self._alert("cluster")
        assert self._downstream(alert.id) is None


class NoRouteFailsTheRunTests(TestCase):
    """An alert nothing claims fails the run instead of silently defaulting."""

    def setUp(self):
        # Reproduce the operator state this failure exists for: the seeded
        # catch-all has been deleted, so some traffic has no lane at all.
        clear_lanes()
        self.incident = Incident.objects.create(title="High CPU", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-noroute",
            source="grafana",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
        )

    def _fake_exec(self, pipeline_run, stage, payload, previous_results, incident_id):
        return {
            PipelineStage.INGEST: IngestResult(
                alert_id=self.alert.id, incident_id=self.incident.id, alerts_created=1
            ),
            PipelineStage.CHECK: CheckResult(checks_run=1),
            PipelineStage.ANALYZE: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: NotifyResult(channels_succeeded=1),
        }[stage]

    def _run(self, payload=None):
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            return PipelineOrchestrator().run_pipeline(
                payload=payload or {"payload": {}}, source="grafana"
            )

    def test_unmatched_alert_fails_the_run_non_retryably(self):
        result = self._run()
        assert result.status == "FAILED"
        assert "no_route" in result.final_error.message
        assert result.final_error.retryable is False
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.FAILED
        assert run.last_error_retryable is False
        assert "no_route" in run.last_error_message

    def test_unmatched_alert_does_not_run_downstream_stages(self):
        result = self._run()
        assert PipelineStage.NOTIFY not in result.stages_completed
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.ANALYZE not in result.stages_completed
        assert not StageExecution.objects.filter(
            pipeline_run__run_id=result.run_id, stage=PipelineStage.NOTIFY
        ).exists()

    def test_ingest_is_still_recorded_as_completed(self):
        """The stage that succeeded must survive in the run record.

        Routing is resolved after INGEST is advanced and appended, so a no_route
        failure does not retroactively erase it. ``stages_completed`` is part of
        ``PipelineResult.to_dict()`` and therefore of the API response shape, and
        the ``StageExecution`` row already says INGEST succeeded — an empty list
        would contradict the run's own children.
        """
        result = self._run()
        assert result.stages_completed == [PipelineStage.INGEST]
        assert result.ingest is not None
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.current_stage == PipelineStage.INGEST

    def test_failure_is_attributed_to_routing_not_to_ingest(self):
        """Blaming ingest would send an operator to debug the one stage that worked.

        The same trace records INGEST as SUCCEEDED, so "Stage ingest failed" is a
        message contradicted by its own child row.
        """
        result = self._run()
        assert "Stage routing failed" in result.final_error.message
        assert "Stage ingest failed" not in result.final_error.message
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert "Stage routing failed" in run.last_error_message

    def test_a_matched_lane_with_no_stages_completes_instead_of_raising(self):
        """``[]`` is a route that runs nothing — it is not a no-route."""
        PipelineDefinition.objects.create(name="inbox-only", match=[], priority=1, stages=[])
        result = self._run()
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.INGEST]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.INGESTED

    def test_no_alert_to_route_completes_instead_of_raising(self):
        """A run with no subject alert has nothing to route and must not fail."""
        self.alert.delete()

        def exec_without_alert(pipeline_run, stage, payload, previous_results, incident_id):
            return IngestResult(alert_id=None, incident_id=self.incident.id, alerts_created=0)

        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=exec_without_alert
        ):
            result = PipelineOrchestrator().run_pipeline(payload={"payload": {}}, source="grafana")
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.INGEST]

    def test_resume_of_an_unmatched_run_also_fails_no_route(self):
        """The resume call site must fail the same way, not fall back."""
        run = PipelineRun.objects.create(
            trace_id="t-noroute",
            run_id="r-noroute",
            source="grafana",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.INCOMING_WEBHOOK,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.INGEST,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"alert_id": self.alert.id, "incident_id": self.incident.id},
        )
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            result = PipelineOrchestrator().resume_pipeline(run_id="r-noroute", payload={})
        assert result.status == "FAILED"
        assert "no_route" in result.final_error.message
        assert result.final_error.retryable is False
