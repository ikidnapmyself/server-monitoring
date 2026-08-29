"""Tests for the node/origin backfill data migration.

Exercises the ``forwards`` function directly against the real models: existing
rows get ``origin`` derived from their ``source`` (``cli*`` -> manual, else
incoming_webhook), and ``node`` derived from the incident's alerts (the node FK
lives on ``Alert``, not ``Incident``) when one carries a node.
"""

import importlib
import logging

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


class _FakeRowSet:
    """Just enough queryset surface for the migrations under test."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def order_by(self, field):
        return _FakeRowSet(sorted(self._rows, key=lambda r: getattr(r, field)))

    def prefetch_related(self, *fields):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeApps:
    """Minimal ``apps`` shim: ``get_model`` yields a manager over fixed rows."""

    def __init__(self, rows):
        self._rows = rows

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("orchestration", "PipelineDefinition")
        return type("M", (), {"objects": _FakeRowSet(self._rows)})


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


# --- 0011: channels M2M -> single channel FK ----------------------------------
#
# Same two-layer approach as 0010: stand-in rows for the selection rule, plus a
# real-schema round trip through ``MigrationExecutor``. The M2M cannot be driven
# through the *current* models (it no longer exists post-0011) and ``channel``
# does not exist on the historical pre-0011 model, so the executor's historical
# states are the only way to exercise the migration end to end.
#
# The migration is LOSSY by design: a lane with several channels keeps the one
# delivery already selected and the rest are dropped with the join table.

channel_migration = importlib.import_module(
    "apps.orchestration.migrations.0011_pipelinedefinition_channel"
)


class _FakeChannel:
    def __init__(self, pk, name, is_active=True):
        self.id = pk
        self.name = name
        self.is_active = is_active


class _FakeM2M:
    """Stand-in for ``defn.channels`` supporting the two calls the migration makes."""

    def __init__(self, channels):
        self._channels = list(channels)
        self.set_to = None

    def filter(self, is_active):
        return _FakeM2M([c for c in self._channels if c.is_active == is_active])

    def order_by(self, field):
        return _FakeM2M(sorted(self._channels, key=lambda c: getattr(c, field.lstrip("-"))))

    def first(self):
        return self._channels[0] if self._channels else None

    def all(self):
        return list(self._channels)

    def set(self, values):
        self.set_to = list(values)


class _FakeChannelDefn(_FakeDefn):
    def __init__(self, channels=(), **fields):
        fields.setdefault("name", "lane")  # forwards() names the lane in its warning
        super().__init__(**fields)
        self.channels = _FakeM2M(channels)


def test_channel_forwards_picks_alphabetically_first_active():
    """Mirrors the old delivery rule exactly: active only, ordered by name."""
    row = _FakeChannelDefn(
        channels=[
            _FakeChannel(1, "zulu"),
            _FakeChannel(2, "alpha"),
            _FakeChannel(3, "mike"),
        ],
        channel_id=None,
    )
    channel_migration.forwards(_FakeApps([row]), None)
    assert row.channel_id == 2
    assert row.saved_fields == ["channel"]


def test_channel_forwards_ignores_inactive_channels():
    """An inactive channel was never delivered to, so it must not be adopted."""
    row = _FakeChannelDefn(
        channels=[_FakeChannel(1, "aaa", is_active=False), _FakeChannel(2, "bbb")],
        channel_id=None,
    )
    channel_migration.forwards(_FakeApps([row]), None)
    assert row.channel_id == 2


def test_channel_forwards_leaves_null_when_no_active_channel():
    row = _FakeChannelDefn(channels=[_FakeChannel(1, "aaa", is_active=False)], channel_id=None)
    channel_migration.forwards(_FakeApps([row]), None)
    assert row.channel_id is None
    assert row.saved_fields is None  # no write at all


def _warn(caplog, rows):
    """Run ``forwards`` and return the emitted warning records."""
    with caplog.at_level(logging.WARNING, logger=channel_migration.logger.name):
        channel_migration.forwards(_FakeApps(rows), None)
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# These assert on ``record.args`` -- the logged *facts* -- rather than the rendered
# sentence, so the wording stays free to improve without a test defending the old
# phrasing. Pinning the prose is how a message keeps a wart nobody can fix.


def test_channel_forwards_warns_naming_every_discarded_channel(apps_logging_propagates, caplog):
    """The loss must be auditable in the migrate log, not just the docstring."""
    row = _FakeChannelDefn(
        name="lane-many",
        channels=[_FakeChannel(1, "zulu"), _FakeChannel(2, "alpha"), _FakeChannel(3, "mike")],
        channel_id=None,
    )
    (record,) = _warn(caplog, [row])
    lane, survivor, count, names = record.args
    assert lane == "lane-many"
    assert survivor == "alpha"  # the survivor is named
    assert count == 2
    assert names == "'mike', 'zulu'"  # every casualty is named, deterministically


def test_channel_forwards_warns_when_all_channels_are_discarded(apps_logging_propagates, caplog):
    """An all-inactive lane keeps nothing: a distinct message, and no crash on chosen=None."""
    row = _FakeChannelDefn(
        name="lane-dead",
        channels=[_FakeChannel(1, "off", is_active=False)],
        channel_id=None,
    )
    (record,) = _warn(caplog, [row])
    lane, count, names = record.args
    assert (lane, count, names) == ("lane-dead", 1, "'off'")
    # The survivor slot is absent entirely rather than filled with a bare "none"
    # that reads as a channel *named* none next to a quoted real name.
    assert "keeping no channel" in record.getMessage()
    assert "unused" not in record.getMessage()


def test_channel_forwards_is_silent_when_nothing_is_lost(apps_logging_propagates, caplog):
    """A single-channel lane loses nothing, so it must not emit a scary warning."""
    row = _FakeChannelDefn(name="lane-one", channels=[_FakeChannel(1, "only")], channel_id=None)
    assert _warn(caplog, [row]) == []


def test_channel_forwards_warns_in_deterministic_lane_order(apps_logging_propagates, caplog):
    """Lanes are logged by name so a production migrate log is reproducible."""
    rows = [
        _FakeChannelDefn(
            name=name,
            channels=[_FakeChannel(1, "aa"), _FakeChannel(2, "bb")],
            channel_id=None,
        )
        for name in ("zeta", "alpha", "mid")
    ]
    records = _warn(caplog, rows)
    assert [r.args[0] for r in records] == ["alpha", "mid", "zeta"]


def test_channel_backwards_restores_the_single_channel():
    row = _FakeChannelDefn(channels=[], channel_id=7)
    channel_migration.backwards(_FakeApps([row]), None)
    assert row.channels.set_to == [7]


def test_channel_backwards_skips_rows_without_a_channel():
    row = _FakeChannelDefn(channels=[], channel_id=None)
    channel_migration.backwards(_FakeApps([row]), None)
    assert row.channels.set_to is None


def test_channel_migration_operation_order_is_load_bearing():
    """``channel`` must be added and backfilled before the M2M is dropped."""
    ops = channel_migration.Migration.operations
    assert [type(op).__name__ for op in ops] == ["AddField", "RunPython", "RemoveField"]
    assert ops[0].name == "channel"
    assert ops[2].name == "channels"


_CH_OLD = ("orchestration", "0010_pipelinedefinition_stages")
_CH_NEW = ("orchestration", "0011_pipelinedefinition_channel")


@pytest.mark.django_db(transaction=True)
def test_channel_real_schema_round_trip():
    """Apply and unapply 0011 against the real schema.

    Asserts the lossy contract concretely: a three-channel lane keeps the
    alphabetically-first *active* one, an all-inactive lane keeps none, an empty
    lane keeps none, and the reverse restores the single survivor (only).
    """
    names = ["lane-many", "lane-inactive", "lane-empty"]
    try:
        old_apps = _migrate_to(_CH_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        NC = old_apps.get_model("notify", "NotificationChannel")
        zulu = NC.objects.create(name="zulu", driver="slack", config={})
        alpha = NC.objects.create(name="alpha", driver="slack", config={})
        mike = NC.objects.create(name="mike", driver="slack", config={})
        off = NC.objects.create(name="off", driver="slack", config={}, is_active=False)

        many = PD.objects.create(name="lane-many")
        many.channels.set([zulu, alpha, mike])
        inactive = PD.objects.create(name="lane-inactive")
        inactive.channels.set([off])
        PD.objects.create(name="lane-empty")

        new_apps = _migrate_to(_CH_NEW)
        PD = new_apps.get_model("orchestration", "PipelineDefinition")
        assert PD.objects.get(name="lane-many").channel.name == "alpha"
        assert PD.objects.get(name="lane-inactive").channel is None
        assert PD.objects.get(name="lane-empty").channel is None

        old_apps = _migrate_to(_CH_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        # Lossy: only the survivor comes back — zulu and mike are gone for good.
        assert [c.name for c in PD.objects.get(name="lane-many").channels.all()] == ["alpha"]
        assert list(PD.objects.get(name="lane-inactive").channels.all()) == []
        assert list(PD.objects.get(name="lane-empty").channels.all()) == []
    finally:
        # Always leave the shared test database at the migration graph's HEAD --
        # head-relative on purpose; see the note on the 0010 round trip above.
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        PipelineDefinition.objects.filter(name__in=names).delete()
        NotificationChannel.objects.filter(name__in=["zulu", "alpha", "mike", "off"]).delete()


# --- 0012: seed the two default lanes ----------------------------------------
#
# Same two-layer approach as 0010/0011. This migration adds no columns, so the
# stand-ins can drive ``forwards``/``backwards`` directly through a fake manager,
# while the real-schema round trip proves the seeded rows land in (and leave) the
# actual table with the priorities routing depends on.

seed_migration = importlib.import_module("apps.orchestration.migrations.0012_seed_default_lanes")


class _FakeGetOrCreateManager:
    """Stand-in manager recording ``get_or_create`` / ``filter(...).delete()`` calls."""

    def __init__(self, existing=()):
        self.rows = {name: dict(fields) for name, fields in existing}
        self.created = []
        self.deleted = []

    def get_or_create(self, name, defaults):
        if name in self.rows:
            return self.rows[name], False
        self.rows[name] = dict(defaults)
        self.created.append(name)
        return self.rows[name], True

    def filter(self, **kwargs):
        self._pending = kwargs
        return self

    def delete(self):
        name = self._pending["name"]
        shape = {k: v for k, v in self._pending.items() if k != "name"}
        self.deleted.append(dict(self._pending))
        if self.rows.get(name) == shape:
            self.rows.pop(name)


class _FakeSeedApps:
    def __init__(self, manager):
        self._manager = manager

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("orchestration", "PipelineDefinition")
        return type("M", (), {"objects": self._manager})


def test_seed_forwards_creates_both_lanes():
    manager = _FakeGetOrCreateManager()
    seed_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == ["cluster-nodes", "catch-all"]
    assert manager.rows["cluster-nodes"]["priority"] == 50
    assert manager.rows["cluster-nodes"]["stages"] == ["analyze", "notify"]
    assert manager.rows["catch-all"]["priority"] == 1000
    assert manager.rows["catch-all"]["match"] == []


def test_seed_forwards_leaves_an_existing_lane_untouched():
    """An operator who already configured a lane by that name keeps their row."""
    manager = _FakeGetOrCreateManager(existing=[("catch-all", {"priority": 7, "stages": []})])
    seed_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == ["cluster-nodes"]
    assert manager.rows["catch-all"] == {"priority": 7, "stages": []}


def test_seed_forwards_is_idempotent():
    manager = _FakeGetOrCreateManager()
    seed_migration.forwards(_FakeSeedApps(manager), None)
    manager.created.clear()
    seed_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == []


def test_seed_backwards_deletes_only_rows_matching_the_seeded_shape():
    """Name alone is not enough: an adopted operator row must survive a rollback."""
    manager = _FakeGetOrCreateManager()
    seed_migration.backwards(_FakeSeedApps(manager), None)
    assert [f["name"] for f in manager.deleted] == ["cluster-nodes", "catch-all"]
    # The full seeded shape is part of every delete filter, not just the name.
    assert manager.deleted[1]["priority"] == 1000
    assert manager.deleted[1]["match"] == []
    assert manager.deleted[1]["stages"] == ["check", "analyze", "notify"]


def test_seed_forwards_does_not_mutate_the_lane_table():
    """``forwards`` pops ``name`` off a copy; a shared dict would break re-runs."""
    seed_migration.forwards(_FakeSeedApps(_FakeGetOrCreateManager()), None)
    assert [lane["name"] for lane in seed_migration._LANES] == ["cluster-nodes", "catch-all"]


def test_seed_migration_is_data_only():
    """No schema operations: this migration exists purely to add rows."""
    ops = seed_migration.Migration.operations
    assert [type(op).__name__ for op in ops] == ["RunPython"]


def test_cluster_lane_outranks_the_catch_all():
    """The ordering the seeds depend on, asserted on the data, not on routing.

    ``cluster-nodes`` must also beat the model's default priority of 100, or a
    hand-created lane would silently outrank it.
    """
    by_name = {lane["name"]: lane for lane in seed_migration._LANES}
    assert by_name["cluster-nodes"]["priority"] < 100 < by_name["catch-all"]["priority"]


_SEED_OLD = ("orchestration", "0011_pipelinedefinition_channel")
_SEED_NEW = ("orchestration", "0012_seed_default_lanes")


@pytest.mark.django_db(transaction=True)
def test_seed_real_schema_round_trip():
    """Apply and unapply 0012 against the real schema.

    Also pins the ``get_or_create`` contract on a real row: a pre-existing
    ``catch-all`` survives forwards with its own priority intact.
    """
    from apps.orchestration.models import PipelineDefinition

    try:
        old_apps = _migrate_to(_SEED_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        PD.objects.filter(name__in=["cluster-nodes", "catch-all"]).delete()
        PD.objects.create(name="catch-all", priority=7, match=[], stages=["notify"])

        new_apps = _migrate_to(_SEED_NEW)
        PD = new_apps.get_model("orchestration", "PipelineDefinition")
        cluster = PD.objects.get(name="cluster-nodes")
        assert cluster.priority == 50
        assert cluster.stages == ["analyze", "notify"]
        assert cluster.match == [{"field": "source", "op": "is", "value": "cluster"}]
        assert cluster.is_active is True
        # The operator's existing row is adopted as-is, not overwritten.
        assert PD.objects.get(name="catch-all").priority == 7

        old_apps = _migrate_to(_SEED_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        # The row this migration created is gone...
        assert not PD.objects.filter(name="cluster-nodes").exists()
        # ...but the operator's own catch-all, which forwards merely adopted, is
        # NOT collateral damage. Deleting by name would have destroyed it (and its
        # channel FK) on any rollback, with no way for forwards to bring it back.
        assert PD.objects.get(name="catch-all").priority == 7
    finally:
        # Always leave the shared test database at the migration graph's HEAD --
        # head-relative on purpose; see the note on the 0010 round trip above.
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        # Restore the seeded rows the reverse pass removed: they are part of the
        # post-migration baseline every other test in this worker routes against.
        #
        # This calls forwards() with the LIVE app registry rather than a historical
        # one, which is safe only because 0012 adds no columns -- the current model
        # and the 0012-era model have the same fields. A future migration that
        # changes PipelineDefinition would make this line lie; re-seed through the
        # executor's historical state instead if that day comes.
        PipelineDefinition.objects.filter(name__in=["cluster-nodes", "catch-all"]).delete()
        seed_migration.forwards(django_apps, None)


# --- 0014: seed the hub self-check lane ---------------------------------------
#
# Same two-layer approach as 0012: the fake manager drives forwards/backwards
# directly (this migration adds no columns), and a real-schema round trip proves
# the row lands in the actual table at the priority routing depends on.

hub_migration = importlib.import_module("apps.orchestration.migrations.0014_seed_hub_self_check")


def test_hub_seed_forwards_creates_the_lane():
    manager = _FakeGetOrCreateManager()
    hub_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == ["hub-self-check"]
    row = manager.rows["hub-self-check"]
    assert row["priority"] == 50
    assert row["match"] == [{"field": "origin", "op": "is", "value": "checker_generated"}]
    # Record-only: the run resolves this lane, stamps it and stops. Empty because
    # the cron repeats every 5 minutes, so notifying would mean ~288 identical
    # messages a day; an operator adds "notify" here to page. CHECK is never
    # listed either — it is the entry stage and has already run.
    assert row["stages"] == []
    assert row["is_active"] is True


def test_hub_seed_forwards_leaves_an_existing_lane_untouched():
    manager = _FakeGetOrCreateManager(existing=[("hub-self-check", {"priority": 3, "stages": []})])
    hub_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == []
    assert manager.rows["hub-self-check"] == {"priority": 3, "stages": []}


def test_hub_seed_forwards_is_idempotent():
    manager = _FakeGetOrCreateManager()
    hub_migration.forwards(_FakeSeedApps(manager), None)
    manager.created.clear()
    hub_migration.forwards(_FakeSeedApps(manager), None)
    assert manager.created == []


def test_hub_seed_backwards_deletes_only_rows_matching_the_seeded_shape():
    manager = _FakeGetOrCreateManager()
    hub_migration.backwards(_FakeSeedApps(manager), None)
    assert [f["name"] for f in manager.deleted] == ["hub-self-check"]
    assert manager.deleted[0]["priority"] == 50
    assert manager.deleted[0]["stages"] == []
    assert manager.deleted[0]["match"] == [
        {"field": "origin", "op": "is", "value": "checker_generated"}
    ]


def test_hub_seed_backwards_spares_an_operator_edited_row():
    manager = _FakeGetOrCreateManager(existing=[("hub-self-check", {"priority": 3})])
    hub_migration.backwards(_FakeSeedApps(manager), None)
    assert manager.rows["hub-self-check"] == {"priority": 3}


def test_hub_seed_forwards_does_not_mutate_the_lane_table():
    hub_migration.forwards(_FakeSeedApps(_FakeGetOrCreateManager()), None)
    assert [lane["name"] for lane in hub_migration._LANES] == ["hub-self-check"]


def test_hub_seed_migration_is_data_only():
    assert [type(op).__name__ for op in hub_migration.Migration.operations] == ["RunPython"]


def test_hub_lane_outranks_the_catch_all_and_hand_made_lanes():
    """Priority asserted against the rows it must beat, not in the abstract."""
    seeded = {lane["name"]: lane for lane in seed_migration._LANES}
    hub = hub_migration._LANES[0]
    assert hub["priority"] < 100 < seeded["catch-all"]["priority"]


_HUB_OLD = ("orchestration", "0013_priority_help_text")
_HUB_NEW = ("orchestration", "0014_seed_hub_self_check")


@pytest.mark.django_db(transaction=True)
def test_hub_seed_real_schema_round_trip():
    """Apply and unapply 0014 against the real schema."""
    from apps.orchestration.models import PipelineDefinition

    try:
        old_apps = _migrate_to(_HUB_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        PD.objects.filter(name="hub-self-check").delete()

        new_apps = _migrate_to(_HUB_NEW)
        PD = new_apps.get_model("orchestration", "PipelineDefinition")
        lane = PD.objects.get(name="hub-self-check")
        assert lane.priority == 50
        assert lane.stages == []
        assert lane.match == [{"field": "origin", "op": "is", "value": "checker_generated"}]
        assert lane.is_active is True

        old_apps = _migrate_to(_HUB_OLD)
        PD = old_apps.get_model("orchestration", "PipelineDefinition")
        assert not PD.objects.filter(name="hub-self-check").exists()
    finally:
        # Leave the shared test database at the graph's HEAD; see the 0010/0012 notes.
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        PipelineDefinition.objects.filter(name="hub-self-check").delete()
        hub_migration.forwards(django_apps, None)
        # 0014 re-seeds the row ACTIVE. 0018 retired it, so re-apply that too or
        # this test would hand every later test a rival lane at priority 50.
        retire_migration.forwards(django_apps, None)


# --- 0018: retire the hub self-check lane -------------------------------------
#
# The lane's reason (a five-minute cron re-reporting a still-firing alert) was
# closed by the incident change gate, and once checker alerts adopted
# ``source: cluster`` it became a silent rival to ``cluster-nodes`` at the same
# priority. These run ``forwards``/``backwards`` against the live registry, which
# is safe because the migration adds no columns.

retire_migration = importlib.import_module(
    "apps.orchestration.migrations.0018_retire_hub_self_check"
)


def _seeded_hub_lane(**overrides):
    from apps.orchestration.models import PipelineDefinition

    fields = {"name": "hub-self-check", "is_active": True, **retire_migration.SEEDED_SHAPE}
    fields.update(overrides)
    PipelineDefinition.objects.filter(name=fields["name"]).delete()
    return PipelineDefinition.objects.create(**fields)


@pytest.mark.django_db
def test_retire_forwards_deactivates_the_seeded_row():
    lane = _seeded_hub_lane()
    retire_migration.forwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is False


@pytest.mark.django_db
def test_retire_forwards_never_deletes_the_row():
    """``Incident.pipeline`` is SET_NULL: deleting would blank the lane on history."""
    from apps.orchestration.models import PipelineDefinition

    _seeded_hub_lane()
    retire_migration.forwards(django_apps, None)
    assert PipelineDefinition.objects.filter(name="hub-self-check").exists()


@pytest.mark.django_db
def test_retire_forwards_keeps_an_operator_edited_row_and_says_so(apps_logging_propagates, caplog):
    """An edited row is the operator's; the migration logs that it left it alone."""
    lane = _seeded_hub_lane(stages=["notify"])
    with caplog.at_level(logging.INFO, logger=retire_migration.logger.name):
        retire_migration.forwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is True
    assert [r.args for r in caplog.records if r.name == retire_migration.logger.name] == [
        ("hub-self-check",)
    ]


@pytest.mark.django_db
def test_retire_forwards_is_a_no_op_when_the_row_was_deleted():
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.filter(name="hub-self-check").delete()
    retire_migration.forwards(django_apps, None)
    assert not PipelineDefinition.objects.filter(name="hub-self-check").exists()


@pytest.mark.django_db
def test_retire_forwards_is_idempotent():
    lane = _seeded_hub_lane(is_active=False)
    retire_migration.forwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is False


@pytest.mark.django_db
def test_retire_forwards_says_an_inactive_row_was_already_off(apps_logging_propagates, caplog):
    """Not "it no longer carries the seeded shape" — that would be untrue."""
    _seeded_hub_lane(is_active=False)
    with caplog.at_level(logging.INFO, logger=retire_migration.logger.name):
        retire_migration.forwards(django_apps, None)
    messages = [r.getMessage() for r in caplog.records if r.name == retire_migration.logger.name]
    assert messages == ["Left pipeline definition 'hub-self-check' alone: it is already inactive."]


@pytest.mark.django_db
def test_retire_forwards_marks_the_row_it_switched_off():
    """Shape cannot say WHO turned a lane off, so forwards records that it did."""
    lane = _seeded_hub_lane()
    retire_migration.forwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.tags[retire_migration.RETIRED_BY_KEY] == retire_migration.RETIRED_BY


@pytest.mark.django_db
def test_retire_forwards_keeps_the_tags_already_on_the_row():
    lane = _seeded_hub_lane(tags={"seed_shape": "record-only"})
    retire_migration.forwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.tags["seed_shape"] == "record-only"


@pytest.mark.django_db
def test_retire_backwards_reactivates_the_row_it_switched_off():
    lane = _seeded_hub_lane()
    retire_migration.forwards(django_apps, None)
    retire_migration.backwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is True
    assert retire_migration.RETIRED_BY_KEY not in lane.tags


@pytest.mark.django_db
def test_retire_backwards_leaves_an_operator_disabled_row_off(apps_logging_propagates, caplog):
    """An operator may have disabled the lane themselves before the migration ran.

    It carries no marker, so a rollback must not overrule their decision.
    """
    lane = _seeded_hub_lane(is_active=False)
    with caplog.at_level(logging.INFO, logger=retire_migration.logger.name):
        retire_migration.backwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is False
    assert [r.args for r in caplog.records if r.name == retire_migration.logger.name] == [
        ("hub-self-check",)
    ]


@pytest.mark.django_db
def test_retire_backwards_ignores_junk_in_tags():
    """``tags`` is a JSONField: a fixture can persist a list there."""
    lane = _seeded_hub_lane(is_active=False, tags=["junk"])
    retire_migration.backwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is False


@pytest.mark.django_db
def test_retire_backwards_is_a_no_op_when_the_row_was_deleted():
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.filter(name="hub-self-check").delete()
    retire_migration.backwards(django_apps, None)
    assert not PipelineDefinition.objects.filter(name="hub-self-check").exists()


@pytest.mark.django_db
def test_retire_backwards_leaves_an_edited_row_alone():
    lane = _seeded_hub_lane(is_active=False, priority=3)
    retire_migration.backwards(django_apps, None)
    lane.refresh_from_db()
    assert lane.is_active is False


def test_retire_migration_is_data_only():
    assert [type(op).__name__ for op in retire_migration.Migration.operations] == ["RunPython"]
