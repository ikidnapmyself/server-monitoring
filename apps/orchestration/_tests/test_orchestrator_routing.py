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

        This pins the origin plumbing on the INGEST-entry path. The
        checker-generated path has its own entry stage and is covered by
        ``CheckAsEntryStageRoutingTests``.
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
        assert (
            PipelineOrchestrator()._incident_subject_alert_id(self.incident.id) == self.critical.id
        )

    def test_snapshot_with_neither_id_still_stops_cleanly(self):
        PipelineDefinition.objects.create(name="ca", match=[], priority=1, stages=["notify"])
        result = self._legacy_run({"severity": "critical"})
        assert result.stages_completed == []

    def test_incident_with_no_alerts_yields_no_subject(self):
        empty = Incident.objects.create(title="ghost", severity="info")
        assert PipelineOrchestrator()._incident_subject_alert_id(empty.id) is None


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


class CheckAsEntryStageRoutingTests(TestCase):
    """Task 8: a ``checks_only`` run routes on the alert CHECK produced.

    The entry stage produces an alert, the lane is resolved from that alert, the
    lane's stages run. INGEST is the entry stage for webhook traffic; CHECK is the
    entry stage for the hub's own scheduled ``run_pipeline --checks-only`` cron.
    Before this, such a run stopped at CHECKED: it opened incidents about the
    hub's own disk and memory every five minutes and notified nobody.

    These tests replace the orchestrator's ``executors`` map rather than patching
    ``_execute_stage_with_retry``, so the real retry wrapper runs and writes the
    ``StageExecution`` rows that ``_stage_completed`` reads. Patching one level up
    would leave that table empty and make every "ran exactly once" assertion
    vacuous. The real CHECKER_REGISTRY is never reached.
    """

    def setUp(self):
        self.incident = Incident.objects.create(title="Disk 95%", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-hub-disk",
            source="server-checkers",
            name="DISK Check Alert",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "hub-01"},
        )
        self.calls: list[PipelineStage] = []
        self.check_result_alert_id = self.alert.id

    def _result_for(self, stage):
        return {
            PipelineStage.INGEST: lambda: IngestResult(
                alert_id=self.alert.id, incident_id=self.incident.id, alerts_created=1
            ),
            PipelineStage.CHECK: lambda: CheckResult(
                checks_run=1, alert_id=self.check_result_alert_id
            ),
            PipelineStage.ANALYZE: lambda: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: lambda: NotifyResult(channels_succeeded=1),
        }[stage]()

    def _orchestrator(self):
        outer = self

        class _RecordingExecutor:
            def __init__(self, stage):
                self.stage = stage

            def execute(self, ctx):
                outer.calls.append(self.stage)
                return outer._result_for(self.stage)

        orchestrator = PipelineOrchestrator()
        orchestrator.executors = {
            stage: _RecordingExecutor(stage) for stage in orchestrator.executors
        }
        return orchestrator

    def _run(self, payload=None, origin=PipelineOrigin.CHECKER_GENERATED):
        return self._orchestrator().run_pipeline(
            payload=payload if payload is not None else {"checks_only": True},
            source="server-checkers",
            origin=origin,
        )

    def test_the_seeded_hub_lane_records_and_correlates_without_notifying(self):
        """``hub-self-check`` ships with empty stages: it routes, then stops.

        An empty lane is a route that ends, not a missing route — it must reach
        COMPLETED/CHECKED rather than raising ``no_route``. The correlation is the
        point: the lane is stamped on the incident and the run keeps its trace_id,
        so a hub self-check is traceable in the admin like any other traffic. The
        cron fires every five minutes and a still-firing alert is re-reported each
        time, so notifying by default would mean ~288 identical messages a day.
        """
        result = self._run()
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.CHECK]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.CHECKED
        # The lane that actually ran is recorded on the incident, so this asserts
        # routing resolved to it rather than merely that the row exists.
        assert Incident.objects.get(pk=self.incident.pk).pipeline.name == "hub-self-check"
        assert run.trace_id

    def test_adding_notify_to_the_seeded_lane_enables_paging(self):
        """The description's promise: this is one column edit away from paging."""
        lane = PipelineDefinition.objects.get(name="hub-self-check")
        lane.stages = ["notify"]
        lane.save(update_fields=["stages"])
        result = self._run()
        assert result.stages_completed == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        assert PipelineRun.objects.get(run_id=result.run_id).status == PipelineStatus.NOTIFIED

    def test_the_lane_is_resolved_from_the_check_result_alert(self):
        """A lane matching the subject alert's instance wins, proving alert_id routes."""
        lane = PipelineDefinition.objects.create(
            name="hub-01-lane",
            priority=1,
            match=[{"field": "instance", "op": "is", "value": "hub-01"}],
            stages=["notify"],
        )
        result = self._run()
        assert result.stages_completed == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        assert Incident.objects.get(pk=self.incident.pk).pipeline_id == lane.id

    def test_the_check_snapshot_carries_the_alert_id_routing_reads(self):
        """``alert_id`` reaches StageExecution.output_snapshot, which resume reads back."""
        result = self._run()
        snapshot = StageExecution.objects.get(
            pipeline_run__run_id=result.run_id,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
        ).output_snapshot
        assert snapshot["alert_id"] == self.alert.id

    def test_no_alerts_ends_at_checked_without_raising(self):
        """A clean run touches no alerts, so there is nothing to route.

        This is the empty-batch path. ``--no-incidents`` reaches CHECKED too but
        by a different route — it has a subject alert and declines to route it;
        see NoIncidentsIsADiagnosticRunTests.
        """
        self.check_result_alert_id = None
        result = self._run(payload={"checks_only": True})
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.CHECK]
        assert PipelineRun.objects.get(run_id=result.run_id).status == PipelineStatus.CHECKED
        assert self.calls == [PipelineStage.CHECK]

    def test_unmatched_checks_only_run_fails_no_route_with_check_recorded(self):
        """Same failure as the ingest path — and CHECK is not erased from the run."""
        clear_lanes()
        result = self._run()
        assert result.status == "FAILED"
        assert "no_route" in result.final_error.message
        assert result.final_error.retryable is False
        # The stage demonstrably succeeded; routing failed after it.
        assert result.stages_completed == [PipelineStage.CHECK]
        assert StageExecution.objects.filter(
            pipeline_run__run_id=result.run_id,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
        ).exists()

    def test_a_lane_that_lists_check_runs_check_exactly_once(self):
        """Deliberately unguarded: ``_stage_completed`` skips the re-entry.

        Pure data, no special case — a lane may name any stage, and the entry
        stage that already succeeded is simply skipped on the second pass. What is
        NOT left to the data is routing: it resolves once per run, or this lane
        would append itself to ``active_stages`` forever.
        """
        clear_lanes()
        PipelineDefinition.objects.create(
            name="checker-with-check",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["check", "analyze"],
        )
        result = self._run()
        assert self.calls == [PipelineStage.CHECK, PipelineStage.ANALYZE]
        assert result.stages_completed == [PipelineStage.CHECK, PipelineStage.ANALYZE]
        assert (
            StageExecution.objects.filter(
                pipeline_run__run_id=result.run_id, stage=PipelineStage.CHECK
            ).count()
            == 1
        )

    def test_deleting_the_hub_lane_falls_through_to_the_catch_all(self):
        """No guard keeps ``hub-self-check`` alive; the catch-all then claims it.

        The catch-all lists ``check``, which is why the skip above matters: the
        run still executes CHECK once and continues to analyze and notify.
        """
        PipelineDefinition.objects.filter(name="hub-self-check").delete()
        result = self._run()
        assert self.calls == [
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert result.stages_completed == [
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert Incident.objects.get(pk=self.incident.pk).pipeline.name == "catch-all"

    def test_an_ingest_run_does_not_re_route_at_check(self):
        """Only the entry stage routes. CHECK inside a webhook run must not."""
        clear_lanes()
        PipelineDefinition.objects.create(
            name="webhook-lane", priority=1, match=[], stages=["check", "analyze"]
        )
        result = self._run(payload={"payload": {}}, origin=PipelineOrigin.INCOMING_WEBHOOK)
        assert result.stages_completed == [
            PipelineStage.INGEST,
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
        ]
        assert self.calls.count(PipelineStage.ANALYZE) == 1

    def test_resume_of_a_checks_only_run_routes_from_the_check_snapshot(self):
        """The resume branch mirrors ingest's: a resumed hub run still routes.

        It must restore BOTH values the snapshot holds. Restoring only alert_id
        routes correctly and still leaves ``incident_id`` None all the way to
        notify, which is how a resumed run ended up delivering to the wrong
        channel; ``CheckerGeneratedNotifiesThroughItsLaneChannelTests`` pins that
        consequence, this pins the value itself.
        """
        clear_lanes()
        PipelineDefinition.objects.create(
            name="resume-lane",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["analyze", "notify"],
        )
        run = PipelineRun.objects.create(
            trace_id="t-hub-resume",
            run_id="r-hub-resume",
            source="server-checkers",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.CHECKER_GENERATED,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"alert_id": self.alert.id, "incident_id": self.incident.id},
        )
        result = self._orchestrator().resume_pipeline(
            run_id="r-hub-resume", payload={"checks_only": True}
        )
        assert result.stages_completed == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
        assert self.calls == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
        assert PipelineRun.objects.get(pk=run.pk).status == PipelineStatus.NOTIFIED
        assert result.incident_id == self.incident.id

    def test_a_legacy_check_snapshot_without_alert_id_resumes_to_checked(self):
        """No legacy lookup for CHECK: a pre-Task-8 checks_only run never routed."""
        run = PipelineRun.objects.create(
            trace_id="t-hub-legacy",
            run_id="r-hub-legacy",
            source="server-checkers",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.CHECKER_GENERATED,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"checks_run": 1},
        )
        result = self._orchestrator().resume_pipeline(
            run_id="r-hub-legacy", payload={"checks_only": True}
        )
        assert result.stages_completed == []
        assert self.calls == []
        assert PipelineRun.objects.get(pk=run.pk).status == PipelineStatus.CHECKED


class CheckerGeneratedNotifiesThroughItsLaneChannelTests(TestCase):
    """A checker-generated run delivers to the channel its lane names.

    This is the payoff of carrying ``incident_id`` out of CHECK. NotifyExecutor
    finds the lane through ``ctx.incident_id -> incident.pipeline``; without it
    the lane's channel FK is dead for this traffic and NotifySelector silently
    falls back to "first active channel by name".

    The two channels are named so that the fallback and the routed answer differ:
    ``aaa-fallback`` sorts first and is what an unrouted run picks, so asserting
    the delivery went to ``zzz-lane-channel`` asserts the routing OUTCOME rather
    than merely that the lane row exists.

    The real NotifyExecutor runs; only the outbound driver send is stubbed.
    """

    def setUp(self):
        from apps.notify.models import NotificationChannel

        clear_lanes()
        self.fallback = NotificationChannel.objects.create(
            name="aaa-fallback", driver="generic", is_active=True, config={}
        )
        self.lane_channel = NotificationChannel.objects.create(
            name="zzz-lane-channel", driver="generic", is_active=True, config={}
        )
        self.lane = PipelineDefinition.objects.create(
            name="hub-lane",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["notify"],
            channel=self.lane_channel,
        )
        self.incident = Incident.objects.create(title="Disk 95%", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-hub-chan",
            source="server-checkers",
            name="DISK Check Alert",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
        )

    def _run(self):
        outer = self

        class _FakeCheckExecutor:
            def execute(self, ctx):
                return CheckResult(
                    checks_run=1,
                    alert_id=outer.alert.id,
                    incident_id=outer.incident.id,
                    alert_fingerprint=outer.alert.fingerprint,
                )

        orchestrator = PipelineOrchestrator()
        orchestrator.executors[PipelineStage.CHECK] = _FakeCheckExecutor()
        # Every outbound driver is stubbed: reaching a live driver would deliver
        # a real message.
        with (
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True, "message_id": "stub-1"},
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value={"success": True, "message_id": "stub-1"},
            ),
        ):
            return orchestrator.run_pipeline(
                payload={"checks_only": True},
                source="server-checkers",
                origin=PipelineOrigin.CHECKER_GENERATED,
            )

    def test_delivery_goes_to_the_channel_the_lane_names(self):
        result = self._run()
        assert result.stages_completed == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        assert [d["driver"] for d in result.notify.deliveries] == ["zzz-lane-channel"]
        assert result.notify.channels_succeeded == 1

    def test_the_run_and_its_signals_carry_the_incident(self):
        """incident_id reaches the run record, so signal tags stop being null."""
        result = self._run()
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.incident_id == self.incident.id
        assert run.alert_fingerprint == self.alert.fingerprint
        assert result.incident_id == self.incident.id

    def test_a_resumed_run_also_delivers_to_the_lane_channel(self):
        """The defect this class exists to catch, on the resume path.

        A resumed run rebuilds ``incident_id`` from the CHECK snapshot rather
        than from a stage result, so it is a second, independent chance to lose
        it — and it did: the restore block read ``alert_id`` only, the run routed
        correctly, and notify still delivered to ``aaa-fallback`` because
        ``ctx.incident_id`` was None. Asserting the stage list alone passes
        throughout; only the delivered channel catches it.
        """
        run = PipelineRun.objects.create(
            trace_id="t-chan-resume",
            run_id="r-chan-resume",
            source="server-checkers",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.CHECKER_GENERATED,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"alert_id": self.alert.id, "incident_id": self.incident.id},
        )
        with (
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True, "message_id": "stub-1"},
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value={"success": True, "message_id": "stub-1"},
            ),
        ):
            result = PipelineOrchestrator().resume_pipeline(
                run_id="r-chan-resume", payload={"checks_only": True}
            )
        assert result.stages_completed == [PipelineStage.NOTIFY]
        # Delivery first: it is the assertion that fails informatively, naming the
        # wrong channel rather than a null id.
        assert [d["driver"] for d in result.notify.deliveries] == ["zzz-lane-channel"]
        assert result.incident_id == self.incident.id

    def test_an_inactive_lane_channel_falls_back(self):
        """``routed_channel`` is the one rule for "active"; notify honours it."""
        self.lane_channel.is_active = False
        self.lane_channel.save(update_fields=["is_active"])
        result = self._run()
        assert [d["driver"] for d in result.notify.deliveries] == ["aaa-fallback"]


class NoIncidentsIsADiagnosticRunTests(TestCase):
    """``--checks-only --no-incidents`` means "just check, don't disturb anything".

    It ends at CHECKED: no lane, no ANALYZE, no NOTIFY. Alert creation is NOT
    suppressed — the bridge still records what it found, which is all
    ``--no-incidents`` ever meant — so this is not the empty-batch path: the run
    has a perfectly routable subject alert and declines to route it.

    ``CheckAlertBridge`` is stubbed, so the real CHECKER_REGISTRY never runs.
    """

    def setUp(self):
        self.incident = Incident.objects.create(title="Disk 95%", severity="critical")
        self.alert = Alert.objects.create(
            fingerprint="fp-diag",
            source="server-checkers",
            name="DISK Check Alert",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
        )
        self.calls: list[PipelineStage] = []

    def _run(self, no_incidents):
        outer = self

        class _FakeBridge:
            def __init__(self, **kwargs):
                outer.bridge_kwargs = kwargs

            def run_checks_and_alert(self, **kwargs):
                from apps.alerts.check_integration import CheckAlertResult

                # A host with a real problem: the bridge opened an alert.
                return CheckAlertResult(checks_run=1, alerts_created=1, alerts=[outer.alert])

        class _Recording:
            def __init__(self, stage, inner):
                self.stage, self.inner = stage, inner

            def execute(self, ctx):
                outer.calls.append(self.stage)
                return self.inner.execute(ctx)

        orchestrator = PipelineOrchestrator()
        # Real executors throughout, wrapped only to record which ones ran.
        orchestrator.executors = {
            stage: _Recording(stage, executor) for stage, executor in orchestrator.executors.items()
        }
        payload = {"checks_only": True, "checker_names": ["cpu"]}
        if no_incidents:
            payload["no_incidents"] = True
        # Every outbound call is stubbed UNCONDITIONALLY, including on the
        # no_incidents runs that are supposed never to reach ANALYZE or NOTIFY.
        # A test must not depend on the code under test to keep it off the
        # network: break the scoping and these runs would otherwise call the live
        # Claude API and deliver a real Slack message. The assertion that they
        # did not run is ``self.calls``, not the absence of a stub.
        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", _FakeBridge),
            patch(
                "apps.orchestration.executors.AnalyzeExecutor.execute",
                return_value=AnalyzeResult(summary="s"),
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True},
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send", return_value={"success": True}
            ),
        ):
            return orchestrator.run_pipeline(
                payload=payload,
                source="server-checkers",
                origin=PipelineOrigin.CHECKER_GENERATED,
            )

    def test_no_incidents_run_ends_at_checked_without_routing(self):
        result = self._run(no_incidents=True)
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.CHECK]
        assert self.calls == [PipelineStage.CHECK]
        assert PipelineRun.objects.get(run_id=result.run_id).status == PipelineStatus.CHECKED
        # Not the empty-batch path: the subject alert exists and would have routed.
        assert result.check.alert_id == self.alert.id
        # Incident creation is what --no-incidents suppresses, and it still does.
        assert self.bridge_kwargs["auto_create_incidents"] is False

    def test_no_incidents_run_does_not_fail_when_no_lane_exists(self):
        """Declining to route is not ``no_route``: there is no routing decision."""
        clear_lanes()
        result = self._run(no_incidents=True)
        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.CHECK]

    def test_a_webhook_run_is_not_silenced_by_a_no_incidents_payload_key(self):
        """The flag scopes ``--checks-only`` runs only, and that matters.

        ``payload`` is the wrapper the webhook view builds around untrusted
        inbound data. Without the ``checks_only`` conjunction, a top-level
        ``no_incidents`` key would let inbound traffic switch off its own routing
        — a silent way to stop the hub notifying. INGEST-entry runs always route.
        """
        outer = self

        class _FakeIngest:
            def execute(self, ctx):
                outer.calls.append(PipelineStage.INGEST)
                return IngestResult(
                    alert_id=outer.alert.id, incident_id=outer.incident.id, alerts_created=1
                )

        # A lane without ``check``: the seeded catch-all lists it, and CHECK here
        # is the REAL executor, which would run the host's every checker for real.
        clear_lanes()
        PipelineDefinition.objects.create(
            name="webhook-lane", priority=1, match=[], stages=["notify"]
        )

        orchestrator = PipelineOrchestrator()
        orchestrator.executors[PipelineStage.INGEST] = _FakeIngest()
        with (
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True},
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send", return_value={"success": True}
            ),
        ):
            result = orchestrator.run_pipeline(
                payload={"payload": {}, "no_incidents": True},
                source="grafana",
                origin=PipelineOrigin.INCOMING_WEBHOOK,
            )
        assert result.stages_completed == [PipelineStage.INGEST, PipelineStage.NOTIFY]

    def test_a_resumed_diagnostic_run_still_declines_to_route(self):
        """The resume call site is scoped by the same flag, not just the fresh one."""
        run = PipelineRun.objects.create(
            trace_id="t-diag-resume",
            run_id="r-diag-resume",
            source="server-checkers",
            status=PipelineStatus.FAILED,
            origin=PipelineOrigin.CHECKER_GENERATED,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage=PipelineStage.CHECK,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"alert_id": self.alert.id, "incident_id": self.incident.id},
        )
        with (
            patch(
                "apps.orchestration.executors.AnalyzeExecutor.execute",
                return_value=AnalyzeResult(summary="s"),
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True},
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send", return_value={"success": True}
            ),
        ):
            result = PipelineOrchestrator().resume_pipeline(
                run_id="r-diag-resume",
                payload={"checks_only": True, "no_incidents": True},
            )
        assert result.stages_completed == []
        assert PipelineRun.objects.get(pk=run.pk).status == PipelineStatus.CHECKED

    def test_the_same_run_without_no_incidents_routes_and_notifies(self):
        """The scheduled cron run is unaffected — this is the contrast case.

        Uses an operator lane that lists the downstream stages: the seeded
        ``hub-self-check`` is deliberately record-only, so routing to it would
        also end at CHECKED and the two cases would be indistinguishable.
        """
        clear_lanes()
        PipelineDefinition.objects.create(
            name="paging-lane",
            priority=1,
            match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
            stages=["analyze", "notify"],
        )
        result = self._run(no_incidents=False)
        assert result.stages_completed == [
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        ]
        assert self.bridge_kwargs["auto_create_incidents"] is True
