"""The design's acceptance list for incident fan-out, end to end.

These drive ``PipelineOrchestrator.run_pipeline`` — the synchronous entry point,
which drains the children it enqueues — and assert on ``PipelineRun`` and
``StageExecution`` rows rather than on mocks. Nothing here patches the
orchestrator, the gate, routing or the executors; only the two outbound edges are
stubbed, for the reason given on ``_stub_outbound``.

The point of asserting on rows is that a mock can be satisfied by a call that
would not have happened in production. A run either exists in the table with the
stages it executed, or the feature does not work.

See docs/plans/2026-08-19-incident-fanout-design.md §6.
"""

import json
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.alerts.models import Incident
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.orchestrator import PipelineOrchestrator


def node_push(alerts, instance_id="web-03"):
    """A cluster-shaped push: what a node's ``push_to_hub`` actually sends."""
    return {
        "source": "cluster",
        "instance_id": instance_id,
        "hostname": instance_id,
        "version": "1.0",
        "alerts": alerts,
    }


def checker_alert(checker, *, severity="critical", status="firing", metrics=None):
    alert = {
        "fingerprint": f"{checker}-web-03",
        "name": f"{checker} alert",
        "status": status,
        "severity": severity,
        "started_at": "2026-08-19T12:00:00Z",
        "labels": {"checker": checker, "hostname": "web-03"},
        "annotations": {"message": f"{checker} says something"},
    }
    if metrics is not None:
        alert["metrics"] = metrics
    return alert


class FanOutAcceptanceTests(TestCase):
    """One test per acceptance criterion in the design."""

    def setUp(self):
        # A lane that both analyses and notifies, so "did ANALYZE run?" is a real
        # question rather than one the routing table has already answered no to.
        # It sits above the seeded resolved lane's 40 for firing traffic only, so
        # the resolved lane still wins for an all-clear.
        PipelineDefinition.objects.create(
            name="e2e-firing",
            match=[{"field": "status", "op": "is", "value": "firing"}],
            stages=["analyze", "notify"],
            priority=30,
            is_active=True,
        )
        # A test database has no active channel, so the seeded lanes are seeded
        # record-only: NOTIFY stripped, and `resolved-all-clear` — which exists
        # only to notify — switched off. Configuring a channel is the operator
        # saying "deliver", so these acceptance runs need that decision made:
        # `enable_delivery` restores NOTIFY on the seeded lanes and binds every
        # delivering lane, this file's own included, to the channel. Without it a
        # lane that lists NOTIFY fails `no_channel`.
        from apps.notify.models import NotificationChannel
        from apps.orchestration.seeding import enable_delivery

        self.channel = NotificationChannel.objects.create(
            name="e2e-ops", driver="generic", is_active=True, config={}
        )
        enable_delivery(PipelineDefinition, self.channel)

    def _stub_outbound(self, stack):
        """Stub the LLM call and the message send — nothing else.

        A test must not depend on the code under test to keep it off the network:
        without these, an acceptance run would call a live provider and deliver a
        real message. What ran is asserted from ``StageExecution`` rows, so the
        stubs remove the I/O without removing the evidence.
        """
        from apps.orchestration.dtos import AnalyzeResult

        stack.enter_context(
            patch(
                "apps.orchestration.executors.AnalyzeExecutor.execute",
                return_value=AnalyzeResult(summary="stub"),
            )
        )
        stack.enter_context(
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value={"success": True, "message_id": "stub"},
            )
        )
        stack.enter_context(
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value={"success": True, "message_id": "stub"},
            )
        )

    def push(self, alerts):
        with ExitStack() as stack:
            self._stub_outbound(stack)
            return PipelineOrchestrator().run_pipeline(
                payload={"driver": "cluster", "payload": node_push(alerts)},
                source="cluster",
            )

    @staticmethod
    def children(result):
        return PipelineRun.objects.filter(trace_id=result.trace_id).exclude(run_id=result.run_id)

    @staticmethod
    def stages_of(run):
        return list(
            StageExecution.objects.filter(pipeline_run=run, status=StageStatus.SUCCEEDED)
            .order_by("id")
            .values_list("stage", flat=True)
        )

    # 1 ---------------------------------------------------------------------
    def test_three_firing_incidents_produce_three_downstream_runs(self):
        """The defect this feature exists for: two of three used to be dropped."""
        result = self.push([checker_alert("cpu"), checker_alert("disk"), checker_alert("memory")])

        children = self.children(result)
        assert children.count() == 3
        assert sorted(c.incident_id for c in children) == sorted(
            Incident.objects.values_list("id", flat=True)
        )
        # Each resolved its own lane and ran it — not one shared verdict.
        for child in children:
            assert self.stages_of(child) == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
            assert child.status == PipelineStatus.NOTIFIED
        assert Incident.objects.count() == 3

    # 2 ---------------------------------------------------------------------
    def test_an_unchanged_repush_produces_none(self):
        """The gate: steady-state re-pushes are what would otherwise flood."""
        alerts = [checker_alert("cpu"), checker_alert("disk"), checker_alert("memory")]
        self.push(alerts)

        result = self.push(alerts)

        assert self.children(result).count() == 0
        assert result.status == "COMPLETED"
        # The push itself still ran and still updated the alerts.
        assert PipelineRun.objects.get(run_id=result.run_id).status == PipelineStatus.INGESTED

    # 3 ---------------------------------------------------------------------
    def test_a_severity_escalation_produces_exactly_one_downstream_run(self):
        self.push([checker_alert("cpu", severity="warning"), checker_alert("disk")])

        result = self.push([checker_alert("cpu", severity="critical"), checker_alert("disk")])

        child = self.children(result).get()
        assert child.incident_id == Incident.objects.get(title__contains="cpu").id

    # 4 ---------------------------------------------------------------------
    def test_a_resolve_notifies_without_analysing(self):
        """The seeded resolved lane, end to end: an all-clear costs no LLM call."""
        self.push([checker_alert("cpu")])

        result = self.push([checker_alert("cpu", severity="info", status="resolved")])

        child = self.children(result).get()
        assert self.stages_of(child) == [PipelineStage.NOTIFY]
        assert not StageExecution.objects.filter(
            pipeline_run=child, stage=PipelineStage.ANALYZE
        ).exists()
        assert Incident.objects.get(pk=child.incident_id).pipeline.name == "resolved-all-clear"

    # 5 ---------------------------------------------------------------------
    def test_a_healthy_push_still_produces_its_parent_run(self):
        """The metrics-egress hook: a clean node must still be seen to report."""
        result = self.push([])

        parent = PipelineRun.objects.get(run_id=result.run_id)
        assert parent.status == PipelineStatus.INGESTED
        assert self.children(result).count() == 0
        assert Incident.objects.count() == 0

    # 6 ---------------------------------------------------------------------
    def test_a_new_unexpected_port_at_unchanged_severity_produces_a_run(self):
        """The case that motivated the context key.

        Severity and status are identical across these three pushes; only the
        flagged port set moves. Without the key the second push would be silent —
        a new service listening on a host would never reach an operator.
        """
        one_port = checker_alert(
            "listening_ports",
            severity="warning",
            metrics={"unexpected_ports": [22], "listening_count": 12},
        )
        two_ports = checker_alert(
            "listening_ports",
            severity="warning",
            metrics={"unexpected_ports": [22, 8080], "listening_count": 13},
        )
        self.push([one_port])

        gained = self.push([two_ports])
        repeated = self.push([two_ports])

        assert self.children(gained).count() == 1
        assert self.children(repeated).count() == 0

    def test_the_same_ports_in_a_different_order_produce_nothing(self):
        """The key is a set, not a string: scan order must not page anyone."""
        forward = checker_alert(
            "listening_ports",
            severity="warning",
            metrics={"unexpected_ports": [22, 8080], "listening_count": 13},
        )
        reversed_ = checker_alert(
            "listening_ports",
            severity="warning",
            metrics={"unexpected_ports": [8080, 22], "listening_count": 13},
        )
        self.push([forward])

        assert self.children(self.push([reversed_])).count() == 0

    def test_the_notification_says_what_the_incident_is(self):
        """Routing correctly and saying nothing are different failures.

        Every stage after the entry stage runs in a downstream run, which has no
        ingest snapshot — so for a while every notification the hub sent read
        "[INFO] monitoring: incident".
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.dtos import AnalyzeResult

        NotificationChannel.objects.create(
            name="e2e-channel", driver="generic", is_active=True, config={}
        )
        sent = []

        def _capture(self, message, *args, **kwargs):
            sent.append(message)
            return {"success": True, "message_id": "stub"}

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "apps.orchestration.executors.AnalyzeExecutor.execute",
                    return_value=AnalyzeResult(summary="stub"),
                )
            )
            stack.enter_context(
                patch("apps.notify.drivers.generic.GenericNotifyDriver.send", _capture)
            )
            PipelineOrchestrator().run_pipeline(
                payload={"driver": "cluster", "payload": node_push([checker_alert("cpu")])},
                source="cluster",
            )

        assert sent, "no notification was delivered"
        assert sent[-1].severity == "critical"
        assert "[CRITICAL]" in sent[-1].title
        assert "cpu" in sent[-1].title.lower()

    def test_a_checker_first_seen_healthy_still_fans_out_when_it_breaks(self):
        """The shape every node produces: OK first, CRITICAL later.

        A node reports every checker every tick, so the hub sees `cpu` resolved on
        push one and opens no incident for it. Fan-out routes on incident ids, so
        the eventual CRITICAL used to produce no downstream run at all.
        """
        self.push([checker_alert("cpu", severity="info", status="resolved")])
        assert Incident.objects.count() == 0

        result = self.push([checker_alert("cpu")])

        child = self.children(result).get()
        assert child.incident_id == Incident.objects.get().id
        assert self.stages_of(child) == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    # 7 ---------------------------------------------------------------------
    def test_children_share_the_parents_trace_and_show_up_in_manage_trace(self):
        """One push is still one story: correlation is free, with no parent FK."""
        result = self.push([checker_alert("cpu"), checker_alert("disk")])

        children = self.children(result)
        assert {c.trace_id for c in children} == {result.trace_id}
        assert len({c.run_id for c in children}) == 2
        assert result.run_id not in {c.run_id for c in children}

        out = StringIO()
        call_command("trace", result.trace_id, "--json", stdout=out)
        traced = json.loads(out.getvalue())

        assert traced["trace_id"] == result.trace_id
        run_ids = {run["run_id"] for run in traced["runs"]}
        assert run_ids == {result.run_id} | {c.run_id for c in children}
        # The projection shows what each run did, so an operator sees the fan-out
        # rather than one run that mysteriously ingested and stopped.
        by_run = {run["run_id"]: [stage for stage, _ in run["stages"]] for run in traced["runs"]}
        assert by_run[result.run_id] == [PipelineStage.INGEST]
        for child in children:
            assert by_run[child.run_id] == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    # 8 ---------------------------------------------------------------------
    def test_a_refire_notifies_as_firing_not_as_an_all_clear(self):
        """The original bug, end to end.

        A refired alert used to stay RESOLVED in the database, so the downstream run
        routed on `status: resolved`, took the seeded resolved-all-clear lane, and
        delivered an all-clear for a CRITICAL problem.
        """
        self.push([checker_alert("cpu")])
        self.push([checker_alert("cpu", severity="info", status="resolved")])

        result = self.push([checker_alert("cpu")])

        child = self.children(result).get()
        assert self.stages_of(child) == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
        incident = Incident.objects.get(pk=child.incident_id)
        assert incident.pipeline.name == "e2e-firing"
        assert incident.status == "open"
