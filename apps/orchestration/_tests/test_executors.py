"""Tests for all stage executors (apps/orchestration/executors.py).

Covers IngestExecutor, CheckExecutor, AnalyzeExecutor, and NotifyExecutor.
"""

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.alerts.check_integration import CheckAlertResult
from apps.alerts.models import Alert, AlertSeverity, Incident, Node
from apps.checkers.checkers import CheckResult as CheckerResult
from apps.checkers.checkers import CheckStatus
from apps.orchestration import inbox
from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    StageContext,
)
from apps.orchestration.executors import (
    AnalyzeExecutor,
    CheckExecutor,
    IngestExecutor,
    NotifyExecutor,
)
from apps.orchestration.models import (
    PipelineOrigin,
    PipelineStage,
    StageExecution,
    StageStatus,
)


def _ctx(payload=None, incident_id=None, previous_results=None, source="test"):
    """Build a minimal StageContext."""
    return StageContext(
        trace_id="trace-abc",
        run_id="run-xyz",
        incident_id=incident_id,
        payload=payload or {},
        previous_results=previous_results or {},
        source=source,
    )


@dataclass
class _FakeProcessingResult:
    """Stand-in for apps.alerts.services.ProcessingResult."""

    alerts_created: int = 1
    alerts_updated: int = 0
    alerts_resolved: int = 0
    incidents_created: int = 1
    incidents_updated: int = 0
    errors: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    material_alerts: list = field(default_factory=list)


def _mock_alert(*, name, severity, fingerprint, pk=1, incident_id=None, title=None):
    """A duck-typed stand-in for an Alert row (``name`` needs explicit assignment)."""
    alert = MagicMock()
    alert.id = pk
    alert.name = name
    alert.severity = severity
    alert.fingerprint = fingerprint
    alert.incident_id = incident_id
    if title is None:
        alert.incident = None
    else:
        alert.incident.title = title
    return alert


# ── IngestExecutor ────────────────────────────────────────────────────────


class TestIngestExecutorSuccess(TestCase):
    def test_successful_ingest(self):
        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = _FakeProcessingResult(
            alerts=[
                _mock_alert(
                    name="disk",
                    severity="critical",
                    fingerprint="fp-123",
                    pk=7,
                    incident_id=42,
                    title="Disk full on web-01",
                )
            ]
        )

        with patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch):
            result = IngestExecutor().execute(
                _ctx(payload={"driver": "generic", "payload": {"key": "val"}})
            )

        assert isinstance(result, IngestResult)
        assert result.alerts_created == 1
        assert result.incidents_created == 1
        assert result.alert_id == 7
        assert result.incident_id == 42
        assert result.incident_title == "Disk full on web-01"
        assert result.alert_fingerprint == "fp-123"
        assert result.severity == "critical"
        assert result.source == "test"
        assert result.normalized_payload_ref == "payload:trace-abc:run-xyz:ingest"
        assert result.duration_ms > 0
        # to_dict() lands in StageExecution.output_snapshot (a JSONField), so the
        # whole dict must survive serialization — not just carry the right keys.
        assert json.loads(json.dumps(result.to_dict()))["incident_title"] == "Disk full on web-01"

    def test_incident_without_title_leaves_incident_title_empty(self):
        """When the incident has no title, incident_title stays the default empty string."""
        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = _FakeProcessingResult(
            alerts=[
                _mock_alert(
                    name="disk",
                    severity="critical",
                    fingerprint="fp-123",
                    incident_id=42,
                    title="",
                )
            ]
        )

        with patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch):
            result = IngestExecutor().execute(
                _ctx(payload={"driver": "generic", "payload": {"key": "val"}})
            )

        assert result.incident_id == 42
        assert result.incident_title == ""

    def test_invalid_payload_not_dict(self):
        result = IngestExecutor().execute(
            _ctx(payload={"driver": "generic", "payload": "not a dict"})
        )
        assert "payload must be a JSON object" in result.errors

    def test_missing_payload_key(self):
        result = IngestExecutor().execute(_ctx(payload={"driver": "generic"}))
        assert "payload must be a JSON object" in result.errors

    def test_ingest_with_no_alerts_leaves_subject_unset(self):
        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = _FakeProcessingResult(
            alerts_created=0, incidents_created=0
        )

        with patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch):
            result = IngestExecutor().execute(_ctx(payload={"driver": "generic", "payload": {}}))

        assert result.alert_id is None
        assert result.incident_id is None
        assert not result.errors


class TestIngestExecutorSubjectSelection(TestCase):
    """The run's subject comes from the alerts THIS push touched."""

    def _alert(self, *, name, severity, fingerprint, incident=None):
        from django.utils import timezone

        from apps.alerts.models import Alert

        return Alert.objects.create(
            fingerprint=fingerprint,
            source="test",
            name=name,
            severity=severity,
            started_at=timezone.now(),
            incident=incident,
        )

    def _execute_with(self, alerts, **ctx_kwargs):
        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = _FakeProcessingResult(alerts=alerts)
        with patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch):
            return IngestExecutor().execute(
                _ctx(payload={"driver": "generic", "payload": {"k": "v"}}, **ctx_kwargs)
            )

    def test_ingest_subject_is_the_most_severe_alert_from_this_push(self):
        """A newer, more severe alert outside this push must not become the subject."""
        from apps.alerts.models import Incident

        pushed_incident = Incident.objects.create(title="Pushed", severity="warning")
        pushed = self._alert(
            name="cpu", severity="warning", fingerprint="fp-pushed", incident=pushed_incident
        )
        # Created afterwards → newer received_at, same source, higher severity,
        # but it is NOT part of this push.
        other_incident = Incident.objects.create(title="Other node", severity="critical")
        self._alert(
            name="disk", severity="critical", fingerprint="fp-other", incident=other_incident
        )

        result = self._execute_with([pushed])

        assert result.alert_id == pushed.id
        assert result.incident_id == pushed_incident.id
        assert result.incident_title == "Pushed"
        assert result.alert_fingerprint == "fp-pushed"
        assert result.severity == "warning"

    def test_ingest_subject_is_most_severe_within_the_push(self):
        low = self._alert(name="cpu", severity="info", fingerprint="fp-low")
        high = self._alert(name="disk", severity="critical", fingerprint="fp-high")

        result = self._execute_with([low, high])

        assert result.alert_id == high.id
        assert result.severity == "critical"

    def test_ingest_subject_ties_break_by_name(self):
        beta = self._alert(name="beta", severity="warning", fingerprint="fp-beta")
        alpha = self._alert(name="alpha", severity="warning", fingerprint="fp-alpha")

        result = self._execute_with([beta, alpha])

        assert result.alert_id == alpha.id

    def test_subject_without_incident_leaves_incident_id_none(self):
        """All-OK pushes create alerts but no incident; the run still completes."""
        alert = self._alert(name="cpu", severity="info", fingerprint="fp-ok")

        result = self._execute_with([alert])

        assert result.alert_id == alert.id
        assert result.incident_id is None
        assert result.incident_title == ""
        assert not result.errors


class TestIngestExecutorSubjectOrderingIsTotal(TestCase):
    """Same name + same severity on two instances must not pick arbitrarily.

    A grouped alertmanager notification can carry one alertname at one severity
    for several instances. Incident grouping is (name, instance)-scoped, so those
    alerts belong to *different* incidents — without a total order the run's
    incident_id and title would swing with payload order.
    """

    def _payload(self, instances):
        return {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "severity": "critical",
                        "instance": instance,
                    },
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": f"fp-{instance}",
                }
                for instance in instances
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def _ingest(self, instances):
        return IngestExecutor().execute(
            _ctx(payload={"driver": "alertmanager", "payload": self._payload(instances)})
        )

    def test_same_name_same_severity_two_instances_is_order_independent(self):
        forward = self._ingest(["a", "b"])
        reverse = self._ingest(["b", "a"])

        assert not forward.errors
        assert not reverse.errors
        # Two hosts, one alertname → two distinct incidents.
        assert forward.alert_fingerprint == "fp-a"
        assert reverse.alert_id == forward.alert_id
        assert reverse.incident_id == forward.incident_id
        assert reverse.alert_fingerprint == forward.alert_fingerprint


class TestIngestExecutorMaterialIncidents(TestCase):
    """Every materially-changed incident leaves the entry stage, not just the subject.

    The subject is one alert; fan-out needs the whole set, or a push carrying two
    hosts' problems only ever diagnoses one of them.
    """

    def _payload(self, instances):
        return {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "severity": "warning",
                        "instance": instance,
                    },
                    "annotations": {"description": "hot"},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": f"fp-{instance}",
                }
                for instance in instances
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def _ingest(self, instances):
        return IngestExecutor().execute(
            _ctx(payload={"driver": "alertmanager", "payload": self._payload(instances)})
        )

    def _execute_with(self, alerts, material):
        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = _FakeProcessingResult(
            alerts=alerts, material_alerts=material
        )
        with patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch):
            return IngestExecutor().execute(
                _ctx(payload={"driver": "generic", "payload": {"k": "v"}})
            )

    def test_ingest_result_carries_every_material_incident(self):
        result = self._ingest(["a", "b"])

        assert not result.errors
        assert len(set(result.material_incident_ids)) == 2
        assert result.incident_id in result.material_incident_ids

    def test_identical_repush_carries_no_material_incident(self):
        self._ingest(["a", "b"])

        assert self._ingest(["a", "b"]).material_incident_ids == []

    def test_material_alert_without_an_incident_is_skipped(self):
        """An alert opened without an incident has no downstream run to enqueue."""
        alert = _mock_alert(name="cpu", severity="info", fingerprint="fp", incident_id=None)

        assert self._execute_with([alert], [alert]).material_incident_ids == []

    def test_the_same_incident_twice_is_carried_once(self):
        """Two alerts of one incident are one unit of work, not two."""
        alerts = [
            _mock_alert(name="cpu", severity="warning", fingerprint="fp-1", pk=1, incident_id=7),
            _mock_alert(name="disk", severity="warning", fingerprint="fp-2", pk=2, incident_id=7),
        ]

        assert self._execute_with(alerts, alerts).material_incident_ids == [7]


class TestIngestExecutorError(SimpleTestCase):
    def test_exception_captured(self):
        with patch(
            "apps.alerts.services.AlertOrchestrator",
            side_effect=RuntimeError("boom"),
        ):
            result = IngestExecutor().execute(_ctx(payload={"driver": "generic", "payload": {}}))

        assert any("Ingest error" in e for e in result.errors)
        assert result.duration_ms > 0


# ── CheckExecutor ─────────────────────────────────────────────────────────


class TestCheckExecutorSuccess(SimpleTestCase):
    def test_successful_check(self):
        @dataclass
        class FakeBridgeResult:
            checks_run: int = 3
            errors: list = field(default_factory=list)
            alerts: list = field(default_factory=list)
            material_alerts: list = field(default_factory=list)
            check_results: list = field(
                default_factory=lambda: [
                    CheckerResult(
                        status=CheckStatus.OK,
                        message="ok",
                        metrics={},
                        checker_name="cpu",
                    ),
                    CheckerResult(
                        status=CheckStatus.WARNING,
                        message="warn",
                        metrics={},
                        checker_name="memory",
                    ),
                    CheckerResult(
                        status=CheckStatus.CRITICAL,
                        message="crit",
                        metrics={},
                        checker_name="disk",
                    ),
                ]
            )

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = FakeBridgeResult()

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx(payload={"checker_names": ["cpu", "memory"]}))

        assert isinstance(result, CheckResult)
        assert result.checks_run == 3
        assert result.checks_passed == 3
        assert result.checks_failed == 0
        assert result.checker_output_ref == "checker:trace-abc:run-xyz:check"

    def test_alert_write_error_does_not_reduce_checks_passed(self):
        @dataclass
        class FakeBridgeResult:
            checks_run: int = 1
            errors: list = field(default_factory=lambda: ["failed to write alert row"])
            alerts: list = field(default_factory=list)
            material_alerts: list = field(default_factory=list)
            check_results: list = field(
                default_factory=lambda: [
                    CheckerResult(
                        status=CheckStatus.OK,
                        message="OK",
                        metrics={},
                        checker_name="cpu",
                    )
                ]
            )

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = FakeBridgeResult()

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert result.checks_failed == 0
        assert result.checks_passed == 1

    def test_unknown_result_does_not_count_as_passed(self):
        bridge_result = CheckAlertResult(
            checks_run=1,
            check_results=[
                CheckerResult(
                    status=CheckStatus.UNKNOWN,
                    message="checker blew up",
                    metrics={},
                    checker_name="disk",
                )
            ],
        )

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = bridge_result

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert result.checks_run == 1
        assert result.checks_passed == 0
        assert result.checks_failed == 1

    def test_warning_and_critical_results_count_as_passed(self):
        bridge_result = CheckAlertResult(
            checks_run=2,
            check_results=[
                CheckerResult(
                    status=CheckStatus.WARNING,
                    message="warm",
                    metrics={},
                    checker_name="cpu",
                ),
                CheckerResult(
                    status=CheckStatus.CRITICAL,
                    message="hot",
                    metrics={},
                    checker_name="disk",
                ),
            ],
        )

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = bridge_result

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert result.checks_run == 2
        assert result.checks_passed == 2

    def test_constructor_failure_cannot_make_checks_passed_negative(self):
        bridge_result = CheckAlertResult(
            checks_run=0,
            errors=["unknown checker: nope"],
            check_results=[],
        )

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = bridge_result

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert result.checks_run == 0
        assert result.checks_passed == 0

    def test_check_results_with_structured_checks(self):
        """Executor maps real CheckResult fields into the checks audit list."""
        cpu_result = CheckerResult(
            status=CheckStatus.WARNING,
            message="CPU at 75%",
            metrics={"cpu_percent": 75.0},
            checker_name="cpu",
        )
        bridge_result = CheckAlertResult(checks_run=1, check_results=[cpu_result])

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = bridge_result

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert len(result.checks) == 1
        assert result.checks[0]["name"] == "cpu"
        assert result.checks[0]["status"] == "warning"
        assert result.checks[0]["message"] == "CPU at 75%"
        assert result.checks[0]["metrics"] == {"cpu_percent": 75.0}


class TestCheckExecutorHostnameAndNoIncidents(SimpleTestCase):
    def test_check_executor_passes_hostname_and_no_incidents(self):
        """CheckExecutor passes hostname and no_incidents to CheckAlertBridge."""
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=1,
            errors=[],
            check_results=[],
            alerts=[],
        )

        ctx = StageContext(
            trace_id="t",
            run_id="r",
            payload={
                "hostname": "web-01",
                "no_incidents": True,
                "checker_names": ["cpu"],
            },
        )

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ) as mock_cls:
            executor = CheckExecutor()
            executor.execute(ctx)

        mock_cls.assert_called_once_with(
            hostname="web-01",
            auto_create_incidents=False,
            trace_id="t",
            register_node=False,
        )

    def test_check_executor_registers_when_the_payload_names_no_host(self):
        """No payload hostname means the bridge is describing THIS machine."""
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=1,
            errors=[],
            check_results=[],
            alerts=[],
        )

        ctx = StageContext(
            trace_id="t",
            run_id="r",
            payload={"checker_names": ["cpu"]},
        )

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ) as mock_cls:
            CheckExecutor().execute(ctx)

        mock_cls.assert_called_once_with(
            auto_create_incidents=True,
            trace_id="t",
            register_node=True,
        )

    def test_check_executor_registers_when_the_payload_hostname_is_blank(self):
        """A blank hostname is no hostname: the bridge falls back to this machine."""
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=1,
            errors=[],
            check_results=[],
            alerts=[],
        )

        ctx = StageContext(
            trace_id="t",
            run_id="r",
            payload={"hostname": "", "checker_names": ["cpu"]},
        )

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ) as mock_cls:
            CheckExecutor().execute(ctx)

        self.assertTrue(mock_cls.call_args.kwargs["register_node"])


class TestCheckExecutorDoesNotRegisterANode(TestCase):
    """Diagnosis runs on the hub but is labelled with the subject's hostname.

    Registering from here would stamp a remote machine's hostname onto this
    machine's own Node row, so CHECK must decline registration.
    """

    def test_check_stage_creates_no_node_for_the_local_instance(self):
        checker_class = MagicMock()
        checker_class.return_value.run.return_value = CheckerResult(
            status=CheckStatus.CRITICAL,
            message="hot",
            metrics={},
            checker_name="cpu",
        )

        ctx = StageContext(
            trace_id="t",
            run_id="r",
            payload={"hostname": "web-01", "checker_names": ["cpu"]},
        )

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"cpu": checker_class},
            clear=True,
        ):
            CheckExecutor().execute(ctx)

        assert Node.objects.count() == 0

    @override_settings(INSTANCE_ID="hub-1")
    def test_check_stage_registers_the_local_node_without_a_payload_hostname(self):
        """``run_pipeline --checks-only`` — the scheduled job — names no host.

        The bridge is then describing this machine, so the hub belongs in its own
        registry: per-node config and the readiness panel both read that row.
        """
        checker_class = MagicMock()
        checker_class.return_value.run.return_value = CheckerResult(
            status=CheckStatus.CRITICAL,
            message="hot",
            metrics={},
            checker_name="cpu",
        )

        ctx = StageContext(trace_id="t", run_id="r", payload={"checker_names": ["cpu"]})

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"cpu": checker_class},
            clear=True,
        ):
            CheckExecutor().execute(ctx)

        assert Node.objects.filter(instance_id="hub-1").exists()


@dataclass
class _FakeAlert:
    """Stand-in for an Alert row: what ``subject_alert`` reads, plus correlation."""

    id: int
    severity: str
    name: str
    fingerprint: str
    incident_id: int | None = None


class TestCheckExecutorSubjectAlert(SimpleTestCase):
    """CHECK reports the subject alert of the batch it just created.

    CHECK is the entry stage for checker-generated runs, so the orchestrator
    routes on this id exactly as it routes on ``IngestResult.alert_id``. Both
    stages use ``routing.subject_alert``; the ordering asserted here is that one
    rule, not a second copy of it.
    """

    @staticmethod
    def _run(alerts):
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=len(alerts),
            errors=[],
            check_results=[],
            alerts=alerts,
        )
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            return CheckExecutor().execute(_ctx(payload={"checker_names": ["cpu"]}))

    def test_most_severe_alert_of_the_batch_becomes_the_subject(self):
        alerts = [
            _FakeAlert(id=11, severity="info", name="aaa", fingerprint="fp-a"),
            _FakeAlert(id=22, severity="critical", name="zzz", fingerprint="fp-z"),
            _FakeAlert(id=33, severity="warning", name="bbb", fingerprint="fp-b"),
        ]
        # Not merely "not None": id 22 is neither first nor last in the list, so a
        # naive alerts[0]/alerts[-1] pick would fail here.
        assert self._run(alerts).alert_id == 22

    def test_severity_ties_break_on_name(self):
        alerts = [
            _FakeAlert(id=11, severity="critical", name="memory", fingerprint="fp-m"),
            _FakeAlert(id=22, severity="critical", name="disk", fingerprint="fp-d"),
        ]
        assert self._run(alerts).alert_id == 22

    def test_no_alerts_leaves_the_subject_unset(self):
        """A clean run touches no alerts, so the run has nothing to route."""
        result = self._run([])
        assert result.alert_id is None
        assert result.incident_id is None
        assert result.alert_fingerprint is None

    def test_subject_incident_and_fingerprint_are_reported(self):
        """notify reads incident_id to find the lane's channel; tags need both."""
        result = self._run(
            [_FakeAlert(id=44, severity="critical", name="disk", fingerprint="fp-d", incident_id=9)]
        )
        assert (result.alert_id, result.incident_id, result.alert_fingerprint) == (44, 9, "fp-d")

    def test_subject_without_an_incident_leaves_incident_id_none(self):
        """An alert the bridge opened without an incident must not raise here."""
        result = self._run([_FakeAlert(id=44, severity="warning", name="cpu", fingerprint="fp-c")])
        assert result.alert_id == 44
        assert result.incident_id is None
        assert result.alert_fingerprint == "fp-c"


class TestCheckExecutorMaterialIncidents(SimpleTestCase):
    """The checker entry stage carries the same set, from its own bridge result."""

    @staticmethod
    def _run(alerts, material):
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=len(alerts),
            errors=[],
            check_results=[],
            alerts=alerts,
            material_alerts=material,
        )
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            return CheckExecutor().execute(_ctx(payload={"checker_names": ["cpu"]}))

    def test_every_material_incident_is_carried(self):
        alerts = [
            _FakeAlert(id=11, severity="warning", name="cpu", fingerprint="fp-c", incident_id=1),
            _FakeAlert(id=22, severity="critical", name="disk", fingerprint="fp-d", incident_id=2),
        ]
        assert sorted(self._run(alerts, alerts).material_incident_ids) == [1, 2]

    def test_unchanged_checks_carry_nothing(self):
        alerts = [
            _FakeAlert(id=11, severity="warning", name="cpu", fingerprint="fp-c", incident_id=1)
        ]
        assert self._run(alerts, []).material_incident_ids == []


class TestCheckExecutorError(SimpleTestCase):
    def test_exception_captured(self):
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            side_effect=RuntimeError("bridge broken"),
        ):
            result = CheckExecutor().execute(_ctx())

        assert any("Check error" in e for e in result.errors)
        assert result.duration_ms > 0


class TestCheckExecutorAllConfigExpansion(SimpleTestCase):
    """CheckExecutor expands the special '__all__' checker_configs key."""

    def _make_bridge(self):
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=2, errors=[], check_results=[]
        )
        return mock_bridge

    def test_all_key_expands_to_per_checker_entries_for_all_registry(self):
        """When checker_names is None, '__all__' expands to every registry checker."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_configs": {
                    "__all__": {"warning_threshold": 60.0, "critical_threshold": 80.0}
                },
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        # "__all__" must be gone; per-checker entries must be present
        assert "__all__" not in configs
        assert configs["cpu"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}
        assert configs["memory"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}

    def test_all_key_expands_only_to_specified_checker_names(self):
        """When checker_names is given, '__all__' expands only to those checkers."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock(), "disk": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_names": ["cpu", "disk"],
                "checker_configs": {"__all__": {"warning_threshold": 70.0}},
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        assert "__all__" not in configs
        assert configs["cpu"] == {"warning_threshold": 70.0}
        assert configs["disk"] == {"warning_threshold": 70.0}
        assert "memory" not in configs

    def test_all_key_merges_with_existing_per_checker_config(self):
        """Per-checker config overrides the __all__ defaults (checker-specific wins)."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_configs": {
                    "__all__": {"warning_threshold": 60.0, "critical_threshold": 80.0},
                    "cpu": {"warning_threshold": 50.0},  # overrides __all__ for cpu
                },
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        assert "__all__" not in configs
        # cpu had an existing entry; __all__ fills in critical_threshold but warning_threshold
        # stays at 50.0 (checker-specific value wins)
        assert configs["cpu"]["warning_threshold"] == 50.0
        assert configs["cpu"]["critical_threshold"] == 80.0
        # memory gets the full __all__ defaults
        assert configs["memory"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}


class _RegistrySwept(BaseException):
    """Not an ``Exception``: no handler on the way can swallow it.

    The bridge's per-checker ``except Exception``, ``CheckExecutor``'s own, and the
    orchestrator's retry wrapper would each turn a plain exception into a recorded
    error string, which is exactly the quiet failure this guard exists to prevent.
    """


class _ExplodingChecker:
    """A registry entry that must never be reached.

    If CHECK sweeps, the test stops here rather than slowly running this
    machine's real disk, memory and temperature probes.
    """

    def __init__(self, *args, **kwargs):
        raise _RegistrySwept("CHECK swept the registry instead of using its subject")


class TestCheckExecutorTakesTheIncidentAsItsSubject(TestCase):
    """An incident run's CHECK diagnoses THAT incident, and nothing else.

    A downstream run's payload is ``{"downstream_incident_id": N}`` — it names no
    checkers — so the scope comes from the incident: the ``checker`` labels its
    alerts carry.
    """

    def setUp(self):
        self.incident = Incident.objects.create(title="cpu on web-03")

    def _alert(self, *, fingerprint, labels):
        return Alert.objects.create(
            fingerprint=fingerprint,
            source="grafana",
            name=fingerprint,
            severity=AlertSeverity.CRITICAL,
            labels=labels,
            started_at=timezone.now(),
            incident=self.incident,
        )

    def _run_names(self, ctx):
        """Execute CHECK with the bridge stubbed; return the checker_names it got."""
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = CheckAlertResult(checks_run=0)
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(ctx)
        assert result.errors == []
        return mock_bridge.run_checks_and_alert.call_args.kwargs["checker_names"]

    def test_check_derives_its_checkers_from_the_incident(self):
        self._alert(fingerprint="check:web-03:cpu", labels={"checker": "cpu"})
        self._alert(fingerprint="check:web-03:memory", labels={"checker": "memory"})

        names = self._run_names(_ctx(incident_id=self.incident.id))

        assert names == ["cpu", "memory"]

    def test_check_runs_nothing_when_the_incident_names_no_checkers(self):
        """A Grafana alert about a room sensor: nothing here corresponds to it."""
        self._alert(fingerprint="room-temp", labels={"room": "server-room"})

        names = self._run_names(_ctx(incident_id=self.incident.id))

        assert names == []

    def test_an_explicit_checker_list_still_wins(self):
        """``check_health`` and ``run_pipeline`` name their own scope; unchanged."""
        self._alert(fingerprint="check:web-03:cpu", labels={"checker": "cpu"})

        names = self._run_names(
            _ctx(payload={"checker_names": ["disk"]}, incident_id=self.incident.id)
        )

        assert names == ["disk"]

    def test_duplicate_checker_labels_run_once(self):
        self._alert(fingerprint="check:web-03:cpu", labels={"checker": "cpu"})
        self._alert(fingerprint="check:web-04:cpu", labels={"checker": "cpu"})

        names = self._run_names(_ctx(incident_id=self.incident.id))

        assert names == ["cpu"]

    def test_a_checker_this_hub_does_not_have_is_no_scope(self):
        """Same rule as no label: this machine cannot diagnose what it cannot run."""
        self._alert(fingerprint="check:web-03:zfs", labels={"checker": "zfs"})

        names = self._run_names(_ctx(incident_id=self.incident.id))

        assert names == []

    def test_another_incidents_alerts_are_not_in_scope(self):
        other = Incident.objects.create(title="disk elsewhere")
        self._alert(fingerprint="check:web-03:cpu", labels={"checker": "cpu"})
        Alert.objects.create(
            fingerprint="check:web-09:disk",
            source="grafana",
            name="disk",
            severity=AlertSeverity.CRITICAL,
            labels={"checker": "disk"},
            started_at=timezone.now(),
            incident=other,
        )

        names = self._run_names(_ctx(incident_id=self.incident.id))

        assert names == ["cpu"]

    def test_a_run_without_an_incident_still_sweeps(self):
        """``--checks-only`` has no incident and no named checkers: unchanged.

        The lane-level scope for such a run is the operator's configuration, and
        deliberately out of scope here.
        """
        names = self._run_names(_ctx())

        assert names is None

    def test_the_registry_is_never_swept_for_an_incident_run(self):
        """The regression guard, end to end: enqueue an incident run and drain it.

        The seeded catch-all lane lists ``check``. Before this fix that meant every
        material change to an unmatched incident ran every checker on the hub —
        alerts about this machine, caused by an incident that has nothing to do
        with it. The bridge is NOT stubbed here: only the registry is, with an
        entry that detonates on instantiation.
        """
        self._alert(fingerprint="room-temp", labels={"room": "server-room"})
        runs = inbox.enqueue_incident_runs(
            [self.incident.id],
            trace_id="t-sweep",
            origin=PipelineOrigin.INCOMING_WEBHOOK,
            source="grafana",
        )

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"cpu": _ExplodingChecker, "disk": _ExplodingChecker},
            clear=True,
        ):
            drained = inbox.drain_runs(runs)

        assert drained == 1
        check = StageExecution.objects.get(
            pipeline_run__run_id=runs[0].run_id, stage=PipelineStage.CHECK
        )
        assert check.status == StageStatus.SUCCEEDED
        assert (check.output_snapshot or {}).get("checks_run") == 0


class TestAnalyzeExecutorExplicitProvider(SimpleTestCase):
    """When payload contains 'provider', get_provider() is used."""

    def test_explicit_provider_calls_get_provider(self):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_provider", return_value=mock_provider
        ) as mock_gp:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider": "local"}))

        mock_gp.assert_called_once_with("local")
        assert isinstance(result, AnalyzeResult)
        assert not result.errors

    def test_explicit_provider_with_config_passes_kwargs(self):
        mock_provider = MagicMock()
        mock_provider.name = "openai"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_provider", return_value=mock_provider
        ) as mock_gp:
            executor = AnalyzeExecutor()
            result = executor.execute(
                _ctx(payload={"provider": "openai", "provider_config": {"model": "gpt-4o"}})
            )

        mock_gp.assert_called_once_with("openai", model="gpt-4o")
        assert isinstance(result, AnalyzeResult)

    def test_model_info_uses_provider_name_attribute(self):
        mock_provider = MagicMock()
        mock_provider.name = "openai"
        mock_provider.run.return_value = []

        with patch("apps.intelligence.providers.get_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider": "openai"}))

        assert result.model_info == {"provider": "openai"}


class TestAnalyzeExecutorActiveProvider(TestCase):
    """When payload has no 'provider', get_active_provider() is used."""

    def test_no_provider_key_calls_get_active_provider(self):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_active_provider", return_value=mock_provider
        ) as mock_gap:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}))

        mock_gap.assert_called_once()
        assert isinstance(result, AnalyzeResult)
        assert not result.errors

    def test_no_provider_key_with_provider_config(self):
        mock_provider = MagicMock()
        mock_provider.name = "claude"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_active_provider", return_value=mock_provider
        ) as mock_gap:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider_config": {"model": "claude-opus"}}))

        mock_gap.assert_called_once_with(model="claude-opus")
        assert result.model_info == {"provider": "claude"}

    def test_model_info_fallback_when_provider_has_no_name(self):
        """When provider has no .name attr and no explicit provider_name, fallback is 'local'."""
        mock_provider = MagicMock(spec=[])  # no attributes at all
        mock_provider.run = MagicMock(return_value=[])

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}))

        assert result.model_info == {"provider": "local"}


class TestAnalyzeExecutorRecommendations(TestCase):
    """Tests for recommendation population and ai_output_ref."""

    def _execute_with_recs(self, recs):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = recs

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            return executor.execute(_ctx(payload={}))

    def test_ai_output_ref_is_set(self):
        result = self._execute_with_recs([])
        assert result.ai_output_ref == "intelligence:trace-abc:run-xyz:analyze"

    def test_empty_recommendations(self):
        result = self._execute_with_recs([])
        assert result.recommendations == []

    def test_recommendation_with_to_dict(self):
        """Objects with to_dict() are serialized via to_dict()."""
        rec = MagicMock()
        rec.to_dict.return_value = {"title": "Fix it", "priority": "high"}
        result = self._execute_with_recs([rec])
        assert result.recommendations == [{"title": "Fix it", "priority": "high"}]

    def test_recommendation_plain_dict_passthrough(self):
        """Plain dicts are passed through as-is."""
        rec = {"title": "Disk full", "priority": "critical"}
        result = self._execute_with_recs([rec])
        assert result.recommendations == [rec]

    def test_recommendation_object_without_to_dict_uses_vars(self):
        """Objects without to_dict() use vars()."""

        class SimpleRec:
            def __init__(self):
                self.title = "mem leak"
                self.priority = "medium"

        result = self._execute_with_recs([SimpleRec()])
        assert result.recommendations[0]["title"] == "mem leak"

    def test_recommendation_non_iterable_fallback(self):
        """Objects with no __dict__ fall back to {'value': str(r)}."""
        result = self._execute_with_recs(["just a string"])
        assert result.recommendations == [{"value": "just a string"}]

    def test_recommendations_with_incident(self):
        from apps.alerts.models import Incident

        incident = Incident.objects.create(title="Test", severity="critical")
        rec = {"title": "Fix it", "priority": "high"}

        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = [rec]

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}, incident_id=incident.id))

        assert result.recommendations == [{"title": "Fix it", "priority": "high"}]
        assert result.confidence == 0.8


class TestAnalyzeExecutorErrorHandling(SimpleTestCase):
    """Tests for error path with fallback_enabled / fallback_disabled."""

    def test_error_with_fallback_enabled(self):
        with patch(
            "apps.intelligence.providers.get_active_provider",
            side_effect=RuntimeError("provider down"),
        ):
            executor = AnalyzeExecutor(fallback_enabled=True)
            result = executor.execute(_ctx(payload={}))

        assert result.fallback_used is True
        assert result.summary == "AI analysis unavailable"
        assert result.errors == []

    def test_error_with_fallback_disabled(self):
        with patch(
            "apps.intelligence.providers.get_active_provider",
            side_effect=RuntimeError("provider down"),
        ):
            executor = AnalyzeExecutor(fallback_enabled=False)
            result = executor.execute(_ctx(payload={}))

        assert result.fallback_used is False
        assert any("Analyze error" in e for e in result.errors)


# ── NotifyExecutor ────────────────────────────────────────────────────────


def _mock_driver_cls(success=True, message_id="msg-1"):
    """Create a mock driver class that returns a configurable send result."""
    driver_instance = MagicMock()
    driver_instance.validate_config.return_value = True
    if success:
        driver_instance.send.return_value = {
            "success": True,
            "message_id": message_id,
        }
    else:
        driver_instance.send.return_value = {
            "success": False,
            "error": "delivery failed",
        }
    driver_cls = MagicMock(return_value=driver_instance)
    return driver_cls, driver_instance


def _resolve_return(driver_cls, channel="default"):
    """Build a NotifySelector.resolve() return tuple."""
    return ("slack", {"webhook_url": "http://x"}, "slack", driver_cls, None, channel)


class TestNotifyExecutorSuccess(TestCase):
    def test_successful_notification(self):
        driver_cls, driver_inst = _mock_driver_cls()
        previous = {
            "analyze": {
                "recommendations": [
                    {"title": "Fix CPU", "priority": "high", "description": "Too hot"}
                ]
            },
            "ingest": {"incident_id": 1, "severity": "critical"},
            "check": {"checks_run": 3, "checks_passed": 3, "checks_failed": 0},
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={"notify_driver": "slack"},
                    previous_results=previous,
                )
            )

        assert isinstance(result, NotifyResult)
        assert result.channels_attempted == 1
        assert result.channels_succeeded == 1
        assert result.channels_failed == 0
        assert not result.errors
        assert result.provider_ids == ["msg-1"]
        assert result.notify_output_ref == "notify:trace-abc:run-xyz:notify"
        assert len(result.messages) == 1
        driver_inst.send.assert_called_once()

    def test_notify_severity_and_title_from_alert_without_ai(self):
        """Severity/title are authoritative from alert data even with no AI recs.

        A checkers-only node with a real critical failure but no AI
        recommendations must still notify at ``critical`` with the incident
        title — never the generic AI-derived ``Incident Analysis`` / ``info``.
        """
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "ingest": {
                "severity": "critical",
                "incident_title": "High CPU on web-03",
                "alerts_created": 2,
                "source": "cluster",
            },
            # AI produced nothing usable (fallback / empty recommendations).
            "analyze": {"fallback_used": True, "recommendations": []},
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results=previous)
            )

        msg = result.messages[0]
        assert msg["severity"] == "critical"
        assert "High CPU on web-03" in msg["title"]
        assert msg["title"] != "Incident Analysis"
        assert msg["message"]

    def test_recommendations_enrich_body_but_not_severity(self):
        """AI recommendations enrich the body only; they never raise severity."""
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "ingest": {
                "severity": "warning",
                "incident_title": "Elevated CPU on web-03",
                "alerts_created": 1,
                "source": "cluster",
            },
            "analyze": {
                "summary": "Restart the CPU-bound worker process",
                "recommendations": [
                    {"title": "Restart worker", "priority": "critical", "description": "hot"}
                ],
            },
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results=previous)
            )

        msg = result.messages[0]
        # Alert-authoritative: AI's critical recommendation did NOT raise it.
        assert msg["severity"] == "warning"
        # AI enrichment appears in the body via the intelligence summary.
        assert "Restart the CPU-bound worker process" in msg["message"]

    def test_no_ingest_data_uses_generated_defaults(self):
        """With no ingest data, title/severity come from derive_headline defaults.

        Severity defaults to ``info`` and the title is generated from the
        source — but it is NOT the old AI-derived ``Incident Analysis``.
        """
        driver_cls, _ = _mock_driver_cls()
        previous = {"analyze": {}}

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results=previous)
            )

        msg = result.messages[0]
        assert msg["severity"] == "info"
        assert msg["title"] != "Incident Analysis"
        assert msg["message"]

    def test_fallback_used_appends_note_but_keeps_alert_headline(self):
        """AI fallback appends a note to the body; title/severity stay from alert."""
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "ingest": {
                "severity": "critical",
                "incident_title": "Disk full on web-01",
                "source": "cluster",
            },
            "analyze": {
                "fallback_used": True,
                "summary": "AI unavailable",
            },
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results=previous)
            )

        msg = result.messages[0]
        assert msg["severity"] == "critical"
        assert "Disk full on web-01" in msg["title"]
        assert "AI analysis unavailable" in msg["message"]


class TestNotifyExecutorDriverFailures(TestCase):
    def test_unknown_driver(self):
        """An unresolvable driver is a routing gap, not a delivery failure.

        It used to land in ``result.errors``, which the orchestrator retries three
        times against a driver that cannot appear.
        """
        from apps.orchestration.errors import StageExecutionError

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=("unknown", {}, "unknown", None, None, "default"),
        ):
            with self.assertRaises(StageExecutionError) as caught:
                NotifyExecutor().execute(
                    _ctx(payload={"notify_driver": "unknown"}, previous_results={"analyze": {}})
                )

        assert caught.exception.retryable is False
        assert "no_driver" in "; ".join(caught.exception.errors)

    def test_invalid_config(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.validate_config.return_value = False

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert any("Invalid configuration" in e for e in result.errors)

    def test_send_exception(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.side_effect = RuntimeError("connection refused")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert result.channels_failed == 1
        assert any("Send error" in e for e in result.errors)
        assert result.deliveries[0]["status"] == "failed"

    def test_send_returns_failure(self):
        driver_cls, _ = _mock_driver_cls(success=False)

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert result.channels_failed == 1
        assert result.channels_succeeded == 0
        assert result.deliveries[0]["status"] == "failed"


class TestNotifyExecutorTemplateRendering(TestCase):
    def test_template_from_channel_config(self):
        driver_cls, _ = _mock_driver_cls()
        channel_obj = MagicMock()
        # Channel config templates are trusted (DB-sourced) but must use the
        # dict form to be rendered as an inline Jinja2 template.
        channel_obj.config = {
            "template": {"type": "inline", "template": "Hello {{ title }}"},
        }

        resolve_ret = ("slack", {}, "slack", driver_cls, channel_obj, "default")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=resolve_ret,
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert not result.errors
        msg = result.messages[0]
        # Template renders {{ title }}, which now comes from derive_headline
        # (alert-authoritative) rather than the old AI-derived "Incident Analysis".
        assert msg["message"].startswith("Hello [")
        assert msg["title"] in msg["message"]

    def test_payload_config_template_is_ignored(self):
        """Templates passed in pipeline payload.notify_config are IGNORED.

        SSTI hardening: only DB-sourced channel config may provide a template.
        Untrusted payload templates must never be rendered. When payload
        contains a template but no channel_obj does, the executor falls back
        to the default ``build_notification_body`` output.
        """
        driver_cls, _ = _mock_driver_cls()

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_driver": "slack",
                        # This template must NOT be rendered; the executor
                        # ignores payload-sourced templates entirely.
                        "notify_config": {"template": "Payload: {{ title }}"},
                    },
                    previous_results={"analyze": {}},
                )
            )

        assert not result.errors
        msg = result.messages[0]
        # The payload-supplied literal string must not appear anywhere; the
        # executor must use the default build_notification_body output.
        assert "Payload: {{ title }}" not in msg["message"]
        assert f"Payload: {msg['title']}" not in msg["message"]
        # Default body is produced from build_notification_body; assert it
        # produced non-empty content so we know the fallback path ran.
        assert msg["message"]

    def test_template_render_error_falls_back(self):
        driver_cls, _ = _mock_driver_cls()
        channel_obj = MagicMock()
        # Broken inline template in channel config: render error should be
        # swallowed and the executor should fall back to the default body.
        channel_obj.config = {
            "template": {"type": "inline", "template": "{{ bad }"},
        }

        resolve_ret = ("slack", {}, "slack", driver_cls, channel_obj, "default")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=resolve_ret,
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        # Falls back to build_notification_body — no error in result
        assert not result.errors
        msg = result.messages[0]
        assert msg["message"]  # non-empty fallback

    def test_payload_ssti_attempt_ignored(self):
        """SSTI regression: a malicious inline template in payload is ignored.

        This is the positive security test that complements
        ``test_payload_config_template_is_ignored``. An attacker-controlled
        payload with a Jinja2 expression must never be rendered.
        """
        driver_cls, _ = _mock_driver_cls()

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_driver": "slack",
                        "notify_config": {"template": "{{ 7*7 }}"},
                    },
                    previous_results={"analyze": {}},
                )
            )

        assert not result.errors
        msg = result.messages[0]
        # The expression must not be evaluated: '49' must not appear, nor the
        # raw template source.
        assert "49" not in msg["message"]
        assert "{{ 7*7 }}" not in msg["message"]

    def test_payload_config_template_keys_stripped_before_resolve(self):
        """Template keys are removed from payload_config before NotifySelector.resolve().

        Drivers receive a config dict without template keys when config originates
        from the payload (no DB channel). This closes the path where a driver
        calling render_message_templates() could pick up payload-supplied template
        values and render attacker-controlled Jinja2 source.
        """
        driver_cls, _ = _mock_driver_cls()
        captured_payload_config = {}

        def _capture_resolve(provider_arg, payload_config=None, requested_channel=None):
            captured_payload_config.update(payload_config or {})
            return _resolve_return(driver_cls)

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            side_effect=_capture_resolve,
        ):
            NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_driver": "slack",
                        "notify_config": {
                            "template": "{{ 7*7 }}",
                            "payload_template": "bad",
                            "html_template": "<b>evil</b>",
                            "text_template": "also bad",
                            "webhook_url": "http://legit.example.com",
                        },
                    },
                    previous_results={"analyze": {}},
                )
            )

        # Template keys must have been stripped; non-template keys must be kept
        assert "template" not in captured_payload_config
        assert "payload_template" not in captured_payload_config
        assert "html_template" not in captured_payload_config
        assert "text_template" not in captured_payload_config
        assert captured_payload_config.get("webhook_url") == "http://legit.example.com"


class TestNotifyExecutorError(SimpleTestCase):
    def test_outer_exception_captured(self):
        with patch(
            "apps.notify.services.NotifySelector.resolve",
            side_effect=RuntimeError("selector broken"),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert any("Notify error" in e for e in result.errors)
        assert result.duration_ms > 0


class TestNotifyExecutorProviderIds(TestCase):
    def test_list_provider_ids(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": ["id-1", "id-2"],
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert result.provider_ids == ["id-1", "id-2"]

    def test_empty_provider_id_not_appended(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": "",
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert result.provider_ids == []
        assert result.channels_succeeded == 1

    def test_numeric_provider_id_coerced(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": 12345,
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "slack"}, previous_results={"analyze": {}})
            )

        assert result.provider_ids == ["12345"]


class TestNotifyExecutorDownstreamHeadline(TestCase):
    """A downstream run has no ingest snapshot, so the headline comes from the incident.

    Fan-out (PR #208) moved NOTIFY into a per-incident downstream run, which never
    runs INGEST. derive_headline read severity and title off the ingest snapshot
    only, so every notification the hub sent afterwards was titled
    "[INFO] monitoring: incident" with the body "monitoring: monitoring event" —
    routed correctly, and saying nothing.
    """

    def _incident(self, severity="critical", title="DISK Check Alert", status="open"):
        from django.utils import timezone

        from apps.alerts.models import Alert, Incident

        incident = Incident.objects.create(title=title, severity=severity, status=status)
        Alert.objects.create(
            fingerprint="fp-d",
            source="cluster",
            name=title,
            severity=severity,
            started_at=timezone.now(),
            incident=incident,
        )
        return incident

    def _notify(self, incident_id, previous=None):
        driver_cls, driver_inst = _mock_driver_cls()
        with patch.dict("apps.notify.views.DRIVER_REGISTRY", {"generic": driver_cls}, clear=False):
            NotifyExecutor().execute(
                _ctx(
                    payload={"notify_driver": "generic"},
                    previous_results=previous or {},
                    incident_id=incident_id,
                )
            )
        return driver_inst.send.call_args[0][0]

    def test_the_headline_comes_from_the_incident(self):
        incident = self._incident()

        message = self._notify(incident.id)

        assert message.severity == "critical"
        assert "DISK Check Alert" in message.title
        assert "[CRITICAL]" in message.title

    def test_a_resolved_incident_keeps_its_own_severity(self):
        """An all-clear is notified through the resolved lane; it is still about a
        critical thing that recovered."""
        incident = self._incident(severity="warning", status="resolved")

        message = self._notify(incident.id)

        assert message.severity == "warning"
        assert message.title.startswith("[RESOLVED]")

    def test_headline_facts_carry_the_incident_status(self):
        """The title says what the incident is now, not only how bad it was."""
        incident = self._incident(status="acknowledged")

        facts = NotifyExecutor._headline_facts(incident)

        assert facts["status"] == "acknowledged"
        assert facts["severity"] == "critical"

    def test_an_ingest_snapshot_still_wins_when_present(self):
        """The push-run path is unchanged: a real snapshot is authoritative."""
        incident = self._incident()

        message = self._notify(
            incident.id,
            previous={"ingest": {"severity": "info", "incident_title": "From snapshot"}},
        )

        assert message.severity == "info"
        assert "From snapshot" in message.title

    def test_the_incident_is_fetched_once(self):
        """The headline and the lane read the same row, not two queries for it."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        incident = self._incident()

        with CaptureQueriesContext(connection) as captured:
            self._notify(incident.id)

        incident_queries = [
            q for q in captured.captured_queries if "alerts_incident" in q["sql"].lower()
        ]
        assert len(incident_queries) == 1, [q["sql"] for q in incident_queries]

    def test_a_vanished_incident_still_sends(self):
        """A run can outlive its incident; that must not stop the message."""
        incident = self._incident()
        incident_id = incident.id
        incident.delete()

        message = self._notify(incident_id)

        assert message.severity == "info"

    def test_no_incident_and_no_snapshot_still_sends(self):
        """A run with neither must not raise; the generic headline is correct there."""
        message = self._notify(None)

        assert message.severity == "info"


class TestNotifyExecutorRoutingGaps(TestCase):
    """A lane that routes to NOTIFY but names no active channel fails loudly.

    The alternative is what this replaces: delivering to whatever channel sorts
    first by name, which is silent and wrong rather than loud and fixable.
    """

    def _incident_on_lane(self, channel=None):
        from django.utils import timezone

        from apps.alerts.models import Alert, Incident
        from apps.orchestration.models import PipelineDefinition

        lane = PipelineDefinition.objects.create(
            name="lane", match=[], stages=["notify"], priority=1, channel=channel
        )
        incident = Incident.objects.create(title="Disk", severity="critical", pipeline=lane)
        Alert.objects.create(
            fingerprint="fp-nc",
            source="cluster",
            name="Disk",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
        )
        return incident

    def test_no_channel_fails_non_retryably(self):
        from apps.orchestration.errors import StageExecutionError

        incident = self._incident_on_lane()

        with self.assertRaises(StageExecutionError) as caught:
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

        self.assertFalse(caught.exception.retryable)
        self.assertIn("no_channel", "; ".join(caught.exception.errors))

    def test_an_inactive_channel_is_no_channel(self):
        """routed_channel() is the one rule for 'active', and notify honours it."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.errors import StageExecutionError

        channel = NotificationChannel.objects.create(
            name="off", driver="generic", is_active=False, config={}
        )
        incident = self._incident_on_lane(channel=channel)

        with self.assertRaises(StageExecutionError):
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

    def test_a_payload_named_driver_still_sends(self):
        """CLI/manual runs that name their own driver are unaffected."""
        driver_cls, driver_inst = _mock_driver_cls()
        incident = self._incident_on_lane()

        with patch.dict("apps.notify.views.DRIVER_REGISTRY", {"generic": driver_cls}, clear=False):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "generic"}, incident_id=incident.id)
            )

        self.assertFalse(result.has_errors)

    def test_an_unregistered_driver_fails_non_retryably(self):
        """A lane naming a driver that does not exist retries three times today.

        No retry can invent a driver, and "Mark for Retry" in the admin spins it
        again — a pointless loop against a typo in a config field.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.errors import StageExecutionError

        channel = NotificationChannel.objects.create(
            name="ghost", driver="does-not-exist", is_active=True, config={}
        )
        incident = self._incident_on_lane(channel=channel)

        with self.assertRaises(StageExecutionError) as caught:
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

        self.assertFalse(caught.exception.retryable)
        self.assertIn("no_driver", "; ".join(caught.exception.errors))
