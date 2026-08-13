"""Tests for the node/origin backfill data migration.

Exercises the ``forwards`` function directly against the real models: existing
rows get ``origin`` derived from their ``source`` (``cli*`` -> manual, else
incoming_webhook), and ``node`` derived from the incident's alerts (the node FK
lives on ``Alert``, not ``Incident``) when one carries a node.
"""

import importlib

import pytest
from django.apps import apps as django_apps

from apps.orchestration.models import PipelineRun

migration = importlib.import_module("apps.orchestration.migrations.0007_backfill_node_origin")


@pytest.mark.django_db
def test_forwards_sets_manual_origin_for_cli_source():
    run = PipelineRun.objects.create(trace_id="t", run_id="cli-run", source="cli-test")
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "manual"


@pytest.mark.django_db
def test_forwards_sets_incoming_origin_for_other_source():
    run = PipelineRun.objects.create(trace_id="t", run_id="web-run", source="grafana")
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "incoming_webhook"


@pytest.mark.django_db
def test_forwards_sets_checker_generated_origin_from_checks_only_payload():
    # A historical --checks-only run carries checks_only in inbound_payload; it must
    # backfill as checker_generated even though its source looks CLI-ish (would be manual).
    run = PipelineRun.objects.create(
        trace_id="t", run_id="checks-run", source="cli", inbound_payload={"checks_only": True}
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "checker_generated"


@pytest.mark.django_db
def test_forwards_leaves_node_null_when_no_alert_has_node():
    """A run whose incident has no node-bearing alert keeps node NULL."""
    from django.utils import timezone

    from apps.alerts.models import Alert, Incident

    incident = Incident.objects.create(title="x", severity="warning")
    Alert.objects.create(
        fingerprint="fp-nonode",
        source="grafana",
        name="A",
        severity="warning",
        status="firing",
        started_at=timezone.now(),
        incident=incident,
    )
    run = PipelineRun.objects.create(
        trace_id="t", run_id="inc-run", source="grafana", incident=incident
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.origin == "incoming_webhook"
    assert run.node is None


@pytest.mark.django_db
def test_forwards_copies_node_from_incident_alert():
    """node is derived from the first node-bearing alert of the run's incident."""
    from django.utils import timezone

    from apps.alerts.models import Alert, Incident, Node

    node = Node.objects.create(instance_id="agent-42")
    incident = Incident.objects.create(title="y", severity="critical")
    Alert.objects.create(
        fingerprint="fp-withnode",
        source="grafana",
        name="B",
        severity="critical",
        status="firing",
        started_at=timezone.now(),
        incident=incident,
        node=node,
    )
    run = PipelineRun.objects.create(
        trace_id="t", run_id="inc-node-run", source="grafana", incident=incident
    )
    migration.forwards(django_apps, None)
    run.refresh_from_db()
    assert run.node_id == node.id


@pytest.mark.django_db
def test_reverse_is_noop():
    """The reverse migration is a no-op and must not raise."""
    run = PipelineRun.objects.create(trace_id="t", run_id="rev-run", source="cli")
    migration.reverse(django_apps, None)
    run.refresh_from_db()
    # reverse leaves rows untouched
    assert run.run_id == "rev-run"


# --- 0010: run_* booleans -> ordered stages list ------------------------------
#
# Unlike 0007, this migration cannot be driven through the *current* models: by
# the time it is applied the booleans no longer exist on ``PipelineDefinition``
# (forwards) and ``stages`` does not exist on the historical pre-0010 model
# (backwards). Two complementary layers cover it instead:
#
#   1. Stand-in rows below exercise the flag <-> list mapping cheaply, table-driven.
#   2. ``test_real_schema_round_trip`` drives the actual migration against the real
#      schema using ``MigrationExecutor``'s historical model states, which is the
#      stronger check: it also catches operation-ordering mistakes, NOT NULL columns
#      added without a default, and ``update_fields`` typos — none of which the
#      stand-ins can see.

stages_migration = importlib.import_module(
    "apps.orchestration.migrations.0010_pipelinedefinition_stages"
)


class _FakeDefn:
    """A stand-in for a historical PipelineDefinition row."""

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.saved_fields = None

    def save(self, update_fields=None):
        self.saved_fields = list(update_fields or [])


class _FakeApps:
    """Minimal ``apps`` shim: ``get_model`` yields a manager over fixed rows."""

    def __init__(self, rows):
        self._rows = rows

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("orchestration", "PipelineDefinition")
        rows = self._rows
        return type("M", (), {"objects": type("Q", (), {"all": staticmethod(lambda: rows)})})


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((True, True, True), ["check", "analyze", "notify"]),
        ((False, True, True), ["analyze", "notify"]),
        ((True, False, True), ["check", "notify"]),
        ((True, True, False), ["check", "analyze"]),
        ((False, False, False), []),
    ],
)
def test_stages_forwards_maps_flags_to_ordered_list(flags, expected):
    check, intel, notify = flags
    row = _FakeDefn(run_checkers=check, run_intelligence=intel, run_notify=notify, stages=None)
    stages_migration.forwards(_FakeApps([row]), None)
    assert row.stages == expected
    assert row.saved_fields == ["stages"]


@pytest.mark.parametrize(
    ("stages", "expected"),
    [
        (["check", "analyze", "notify"], (True, True, True)),
        (["analyze", "notify"], (False, True, True)),
        ([], (False, False, False)),
        (None, (False, False, False)),  # a NULL/absent list reverses to all-off
    ],
)
def test_stages_backwards_maps_list_to_flags(stages, expected):
    row = _FakeDefn(stages=stages)
    stages_migration.backwards(_FakeApps([row]), None)
    assert (row.run_checkers, row.run_intelligence, row.run_notify) == expected
    assert row.saved_fields == ["run_checkers", "run_intelligence", "run_notify"]


def test_stages_round_trips_through_forwards_and_backwards():
    """A full-pipeline row survives forwards -> backwards unchanged."""
    row = _FakeDefn(run_checkers=True, run_intelligence=False, run_notify=True, stages=None)
    stages_migration.forwards(_FakeApps([row]), None)
    assert row.stages == ["check", "notify"]
    stages_migration.backwards(_FakeApps([row]), None)
    assert (row.run_checkers, row.run_intelligence, row.run_notify) == (True, False, True)


def test_stages_migration_operation_order_is_load_bearing():
    """``stages`` must be added and backfilled before the booleans are dropped."""
    ops = stages_migration.Migration.operations
    assert [type(op).__name__ for op in ops] == [
        "AddField",
        "RunPython",
        "RemoveField",
        "RemoveField",
        "RemoveField",
    ]
    assert [op.name for op in ops[2:]] == ["run_checkers", "run_intelligence", "run_notify"]


_OLD = ("orchestration", "0009_alter_pipelinerun_node")
_NEW = ("orchestration", "0010_pipelinedefinition_stages")


def _migrate_to(target):
    """Run the executor to ``target`` and return the resulting historical apps."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()  # reload state after applying/unapplying
    return executor.loader.project_state([target]).apps


@pytest.mark.django_db(transaction=True)
def test_real_schema_round_trip():
    """Apply and unapply 0010 against the real schema, asserting the data survives.

    Uses the historical model states the executor builds, so the pre-0010 model
    still has the booleans and the post-0010 one has ``stages``.
    """
    try:
        old_apps = _migrate_to(_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        PD.objects.create(name="lane-a", run_checkers=True, run_intelligence=False, run_notify=True)
        PD.objects.create(
            name="lane-b", run_checkers=False, run_intelligence=False, run_notify=False
        )

        new_apps = _migrate_to(_NEW)
        PD = new_apps.get_model("orchestration", "PipelineDefinition")
        assert PD.objects.get(name="lane-a").stages == ["check", "notify"]
        assert PD.objects.get(name="lane-b").stages == []

        old_apps = _migrate_to(_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        a, b = PD.objects.get(name="lane-a"), PD.objects.get(name="lane-b")
        assert (a.run_checkers, a.run_intelligence, a.run_notify) == (True, False, True)
        assert (b.run_checkers, b.run_intelligence, b.run_notify) == (False, False, False)
    finally:
        # Always leave the shared test database at the migration graph's HEAD.
        #
        # This must stay head-relative, not a hard-coded target: the moment a later
        # migration is added this file does not know about, restoring to 0010 would
        # strand the database several migrations behind, and every later test in the
        # worker would fail with schema errors that look like they belong to whatever
        # runs next. Do not re-hardcode.
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from apps.orchestration.models import PipelineDefinition

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        PipelineDefinition.objects.filter(name__in=["lane-a", "lane-b"]).delete()
