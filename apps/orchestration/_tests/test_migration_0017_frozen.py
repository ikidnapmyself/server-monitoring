"""The 0017 seed is a historical snapshot and must not move.

The migration used to import ``apps.orchestration.seeding``, so replaying it on a
fresh database ran whatever that module had become — a lane edited next year would
reach fresh installs and no upgraded one, from the same migration number. It now
carries its own frozen copy.

Every expectation below is written out in full rather than compared against
``seeding``: a test that asserts the two are equal would pass a coordinated edit,
which is exactly the failure being prevented. Changing this file's numbers is
changing history, and the only legitimate reason to do it is a new migration.
"""

import importlib
import inspect
import re

from django.test import TestCase

MIGRATION = importlib.import_module("apps.orchestration.migrations.0017_seed_routing_table")


class FrozenLaneShapesTests(TestCase):
    def test_the_lanes_are_exactly_these(self):
        self.assertEqual(
            [
                (lane["name"], lane["stages"], lane["priority"], lane["match"])
                for lane in MIGRATION.LANES
            ],
            [
                (
                    "resolved-all-clear",
                    ["notify"],
                    40,
                    [{"field": "status", "op": "is", "value": "resolved"}],
                ),
                (
                    "cluster-nodes",
                    ["analyze", "notify"],
                    50,
                    [{"field": "source", "op": "is", "value": "cluster"}],
                ),
                (
                    "hub-self-check",
                    [],
                    50,
                    [{"field": "origin", "op": "is", "value": "checker_generated"}],
                ),
                ("catch-all", ["check", "analyze", "notify"], 1000, []),
            ],
        )

    def test_the_prior_shapes_it_repairs_are_exactly_these(self):
        """What 0012/0014/0016 wrote. Widening this would rewrite operator edits."""
        self.assertEqual(
            MIGRATION.PRIOR_STAGES,
            {
                "resolved-all-clear": ["notify"],
                "cluster-nodes": ["analyze", "notify"],
                "hub-self-check": [],
                "catch-all": ["check", "analyze", "notify"],
            },
        )

    def test_the_provenance_marker_is_exactly_this(self):
        """A later action reads this key off the row; renaming it strands old rows."""
        self.assertEqual(MIGRATION.SEED_SHAPE_KEY, "seed_shape")
        self.assertEqual(MIGRATION.RECORD_ONLY, "record-only")

    def test_it_does_not_import_the_live_seed(self):
        """The point of the copy: no import, no drift.

        Matched as an import statement rather than as a mention of the name — the
        docstring names the module deliberately, to explain the duplication.
        """
        source = inspect.getsource(MIGRATION)
        self.assertIsNone(
            re.search(r"^\s*(from|import)\s+apps\.orchestration\.seeding", source, re.MULTILINE)
        )


class FrozenSeedBehaviourTests(TestCase):
    """The snapshot's body, exercised directly — the migration ran long ago here."""

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        MIGRATION._seed(PipelineDefinition, NotificationChannel)

    def _clear(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.all().delete()

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    def _channel(self, name="ops"):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=True, config={}
        )

    def test_a_channel_less_hub_is_seeded_record_only_and_marked(self):
        self._clear()

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])
        self.assertEqual(self._lane("catch-all").tags, {"seed_shape": "record-only"})
        self.assertFalse(self._lane("resolved-all-clear").is_active)

    def test_one_channel_seeds_notify_and_binds_it(self):
        self._clear()
        channel = self._channel()

        self._seed()

        lane = self._lane("catch-all")
        self.assertEqual(lane.stages, ["check", "analyze", "notify"])
        self.assertEqual(lane.channel_id, channel.id)

    def test_several_channels_bind_nothing(self):
        self._clear()
        self._channel("aaa")
        self._channel("zzz")

        self._seed()

        self.assertIsNone(self._lane("catch-all").channel_id)

    def test_it_repairs_the_rows_the_earlier_migrations_left(self):
        """The state a real install presents: the rows already exist."""
        from apps.orchestration.models import PipelineDefinition

        self._clear()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=["check", "analyze", "notify"], priority=1000
        )

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])

    def test_it_never_reactivates_a_disabled_lane(self):
        from apps.orchestration.models import PipelineDefinition

        self._clear()
        self._channel()
        PipelineDefinition.objects.create(
            name="catch-all",
            match=[],
            stages=["check", "analyze", "notify"],
            priority=1000,
            is_active=False,
        )

        self._seed()

        self.assertFalse(self._lane("catch-all").is_active)

    def test_an_edited_lane_is_not_repaired(self):
        from apps.orchestration.models import PipelineDefinition

        self._clear()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=["notify"], priority=1000
        )

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["notify"])

    def test_junk_in_tags_does_not_break_the_marker(self):
        from apps.orchestration.models import PipelineDefinition

        self._clear()
        PipelineDefinition.objects.create(
            name="catch-all",
            match=[],
            stages=["check", "analyze", "notify"],
            priority=1000,
            tags=["not", "a", "dict"],
        )

        self._seed()

        self.assertEqual(self._lane("catch-all").tags, {"seed_shape": "record-only"})

    def test_seeding_twice_changes_nothing(self):
        from apps.orchestration.models import PipelineDefinition

        self._clear()
        self._channel()
        self._seed()
        before = list(PipelineDefinition.objects.values_list("name", "stages", "channel", "tags"))

        self._seed()

        after = list(PipelineDefinition.objects.values_list("name", "stages", "channel", "tags"))
        self.assertEqual(before, after)
