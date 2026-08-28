"""Tests for the channel-aware routing-table seed (apps/orchestration/seeding.py)."""

from django.test import TestCase


class SeedRoutingTableTests(TestCase):
    """The seed reads the hub instead of assuming it.

    Zero channels means "this hub records" — every lane keeps routing, none claims
    to deliver, and nothing fails. One channel means "this hub delivers there".
    Two or more is the only case a human has to resolve.
    """

    def _channel(self, name="ops", is_active=True):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=is_active, config={}
        )

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        PipelineDefinition.objects.all().delete()
        return seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    # --- zero channels: a recording hub -----------------------------------
    def test_no_channel_seeds_lanes_that_do_not_claim_to_deliver(self):
        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])
        self.assertEqual(self._lane("cluster-nodes").stages, ["analyze"])
        self.assertIsNone(self._lane("catch-all").channel_id)

    def test_no_channel_deactivates_the_resolved_lane(self):
        """Its only purpose is to notify an all-clear; active it would just shadow."""
        self._seed()

        self.assertFalse(self._lane("resolved-all-clear").is_active)

    def test_no_channel_still_routes_everything(self):
        """Recording is not silence: alerts still match, check and analyse."""
        from apps.orchestration.models import PipelineDefinition

        self._seed()

        self.assertTrue(PipelineDefinition.objects.filter(is_active=True, match=[]).exists())

    def test_the_report_says_the_hub_is_not_delivering(self):
        """The caller logs this; a recording hub must be visible as a choice."""
        report = self._seed()

        self.assertEqual(report["created"], 3)
        self.assertEqual(report["bound"], 0)
        self.assertFalse(report["delivering"])

    # --- one channel: a delivering hub ------------------------------------
    def test_one_channel_seeds_notify_and_binds_it(self):
        channel = self._channel()

        self._seed()

        lane = self._lane("catch-all")
        self.assertEqual(lane.stages, ["check", "analyze", "notify"])
        self.assertEqual(lane.channel_id, channel.id)
        self.assertTrue(self._lane("resolved-all-clear").is_active)

    def test_one_channel_reports_what_it_bound(self):
        self._channel()

        report = self._seed()

        self.assertEqual(report["bound"], 3)
        self.assertTrue(report["delivering"])

    def test_an_inactive_channel_does_not_count(self):
        self._channel(is_active=False)

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])

    # --- two or more: the operator chooses --------------------------------
    def test_several_channels_seed_notify_but_bind_nothing(self):
        """Alphabetical accident is the bug being removed; there is no right answer."""
        self._channel("aaa")
        self._channel("zzz")

        self._seed()

        lane = self._lane("catch-all")
        self.assertIn("notify", lane.stages)
        self.assertIsNone(lane.channel_id)

    # --- idempotence and operator intent ----------------------------------
    def test_an_existing_lane_is_never_rewritten(self):
        """get_or_create on name: an operator's row is theirs."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        self._channel()
        PipelineDefinition.objects.all().delete()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=[], priority=7, is_active=False
        )

        seed_routing_table(PipelineDefinition, NotificationChannel)

        lane = self._lane("catch-all")
        self.assertEqual(lane.stages, [])
        self.assertEqual(lane.priority, 7)

    def test_an_operator_channel_is_never_replaced(self):
        """Binding fills an empty slot only; a chosen channel stays chosen."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        chosen = self._channel("chosen")
        PipelineDefinition.objects.all().delete()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=["notify"], priority=7, channel=chosen
        )
        NotificationChannel.objects.filter(pk=chosen.pk).update(is_active=False)
        other = self._channel("other")

        seed_routing_table(PipelineDefinition, NotificationChannel)

        self.assertEqual(self._lane("catch-all").channel_id, chosen.id)
        self.assertEqual(self._lane("cluster-nodes").channel_id, other.id)

    def test_seeding_twice_changes_nothing(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        self._channel()

        self._seed()
        before = list(PipelineDefinition.objects.values_list("name", "stages", "channel"))

        seed_routing_table(PipelineDefinition, NotificationChannel)

        after = list(PipelineDefinition.objects.values_list("name", "stages", "channel"))
        self.assertEqual(before, after)

    def test_hub_self_check_lane_is_not_seeded(self):
        """Retired: the hub's own checks route through ``cluster-nodes`` like a node's.

        The lane existed to keep a five-minute cron quiet. The incident change gate
        closed that, and the lane had become a silent rival to ``cluster-nodes`` at
        the same priority once checker alerts moved to ``source: cluster``.
        """
        from apps.orchestration.models import PipelineDefinition

        self._seed()

        self.assertFalse(PipelineDefinition.objects.filter(name="hub-self-check").exists())

    def test_only_delivering_lanes_are_bound(self):
        """A lane is bound because it lists ``notify``, not because it exists."""
        channel = self._channel()

        self._seed()

        for name in ("catch-all", "cluster-nodes", "resolved-all-clear"):
            self.assertEqual(self._lane(name).channel_id, channel.id, name)


class SeedOverPriorMigrationsTests(TestCase):
    """The state the migration ACTUALLY meets: rows 0012/0014/0016 already created.

    Every other test in this module clears the table first, which is the one state
    a fresh install never presents — 0012/0014/0016 run before 0017, so the rows
    exist and `get_or_create`'s defaults are discarded. Seeding into an empty table
    proves nothing about production.
    """

    def _prior_seed(self):
        """Recreate what the earlier migrations leave behind."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.all().delete()
        for name, stages, match, priority in [
            (
                "resolved-all-clear",
                ["notify"],
                [{"field": "status", "op": "is", "value": "resolved"}],
                40,
            ),
            (
                "cluster-nodes",
                ["analyze", "notify"],
                [{"field": "source", "op": "is", "value": "cluster"}],
                50,
            ),
            (
                "hub-self-check",
                [],
                [{"field": "origin", "op": "is", "value": "checker_generated"}],
                50,
            ),
            ("catch-all", ["check", "analyze", "notify"], [], 1000),
        ]:
            PipelineDefinition.objects.create(
                name=name, stages=stages, match=match, priority=priority, is_active=True
            )

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        return seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    def test_a_channel_less_hub_is_repaired_to_record_only(self):
        """Nothing was created, so only repair can shape these rows."""
        self._prior_seed()

        report = self._seed()

        self.assertEqual(report["created"], 0)
        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])
        self.assertEqual(self._lane("cluster-nodes").stages, ["analyze"])
        self.assertFalse(self._lane("resolved-all-clear").is_active)

    def test_a_single_channel_hub_binds_every_delivering_lane(self):
        """Not just the catch-all: node pushes and all-clears take their own lanes."""
        from apps.notify.models import NotificationChannel

        channel = NotificationChannel.objects.create(
            name="ops", driver="generic", is_active=True, config={}
        )
        self._prior_seed()

        self._seed()

        for name in ("catch-all", "cluster-nodes", "resolved-all-clear"):
            self.assertEqual(self._lane(name).channel_id, channel.id, name)
        # 0014 left this row behind and 0018 retired it. The seed no longer lists it,
        # so it must be passed over entirely rather than quietly bound.
        self.assertIsNone(self._lane("hub-self-check").channel_id)

    def test_an_edited_lane_is_not_repaired(self):
        """Repair is for untouched rows; an operator's shape is theirs."""
        self._prior_seed()
        lane = self._lane("catch-all")
        lane.stages = ["notify"]
        lane.save(update_fields=["stages"])

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["notify"])

    def test_repair_is_idempotent(self):
        self._prior_seed()
        self._seed()

        report = self._seed()

        self.assertEqual(report["repaired"], 0)


class BindDeliveringLanesTests(TestCase):
    """setup_cluster binds every lane that promises delivery, not only the catch-all."""

    def _lane(self, name, stages, channel=None):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.create(
            name=name, match=[], stages=stages, priority=100, channel=channel
        )

    def _channel(self, name="ops"):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=True, config={}
        )

    def test_every_unbound_delivering_lane_is_bound(self):
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import bind_delivering_lanes

        PipelineDefinition.objects.all().delete()
        channel = self._channel()
        self._lane("a", ["notify"])
        self._lane("b", ["analyze", "notify"])

        bound = bind_delivering_lanes(PipelineDefinition, channel)

        self.assertEqual(bound, 2)

    def test_a_lane_that_never_delivers_is_skipped(self):
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import bind_delivering_lanes

        PipelineDefinition.objects.all().delete()
        channel = self._channel()
        lane = self._lane("records", ["check", "analyze"])

        bind_delivering_lanes(PipelineDefinition, channel)

        lane.refresh_from_db()
        self.assertIsNone(lane.channel_id)

    def test_a_disabled_lane_is_not_bound(self):
        """A lane an operator switched off routes nothing and needs no channel."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import bind_delivering_lanes

        PipelineDefinition.objects.all().delete()
        channel = self._channel()
        lane = self._lane("off", ["notify"])
        lane.is_active = False
        lane.save(update_fields=["is_active"])

        bind_delivering_lanes(PipelineDefinition, channel)

        lane.refresh_from_db()
        self.assertIsNone(lane.channel_id)

    def test_an_operator_choice_is_never_replaced(self):
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import bind_delivering_lanes

        PipelineDefinition.objects.all().delete()
        chosen = self._channel("chosen")
        other = self._channel("other")
        lane = self._lane("a", ["notify"], channel=chosen)

        bind_delivering_lanes(PipelineDefinition, other)

        lane.refresh_from_db()
        self.assertEqual(lane.channel_id, chosen.id)


class EnableDeliveryTests(TestCase):
    """Configuring a channel is the operator saying "deliver" — at configuration time.

    The pipeline never reinterprets a definition at runtime; it executes it. So a
    hub seeded record-only does not start delivering because notify noticed a
    channel appeared — the definition changes, here, when the operator configures
    one.
    """

    def _channel(self):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name="ops", driver="generic", is_active=True, config={}
        )

    def _record_only_seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        PipelineDefinition.objects.all().delete()
        seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    def test_a_record_only_hub_starts_delivering(self):
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        self._record_only_seed()
        channel = self._channel()

        enable_delivery(PipelineDefinition, channel)

        for name in ("catch-all", "cluster-nodes", "resolved-all-clear"):
            lane = self._lane(name)
            self.assertIn("notify", lane.stages, name)
            self.assertEqual(lane.channel_id, channel.id, name)
            self.assertTrue(lane.is_active, name)

    def test_a_lane_that_never_delivers_is_untouched(self):
        """Restoration and binding both act on ``notify``; a recording lane has none.

        (This used to assert on the seeded ``hub-self-check`` lane, retired in
        ``0018``. Every seeded lane now lists ``notify``, so the case is exercised
        with an operator's own recording lane, which is where it still occurs.)
        """
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        self._record_only_seed()
        PipelineDefinition.objects.create(
            name="records-by-choice", match=[], stages=["check", "analyze"], priority=100
        )
        channel = self._channel()

        enable_delivery(PipelineDefinition, channel)

        lane = self._lane("records-by-choice")
        self.assertEqual(lane.stages, ["check", "analyze"])
        self.assertIsNone(lane.channel_id)

    def test_an_edited_lane_is_left_alone(self):
        """Restoration is for the shape this module wrote, not an operator's."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        self._record_only_seed()
        lane = self._lane("catch-all")
        lane.stages = ["check"]
        lane.save(update_fields=["stages"])
        channel = self._channel()

        enable_delivery(PipelineDefinition, channel)

        self.assertEqual(self._lane("catch-all").stages, ["check"])

    def test_it_is_idempotent(self):
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        self._record_only_seed()
        channel = self._channel()
        enable_delivery(PipelineDefinition, channel)

        self.assertEqual(enable_delivery(PipelineDefinition, channel), 0)


class SeedShapeMarkerTests(TestCase):
    """Provenance, not shape-guessing: the seed records what it did.

    ``stages`` cannot answer "did the seed write this?" — an operator can type the
    same list by hand, and an operator can edit a list the seed wrote. ``tags`` is
    a JSON dict on the row, so the seed marks the lanes it shaped record-only and
    later configuration-time actions read the marker instead of re-deriving intent
    from a shape.
    """

    def _channel(self, name="ops"):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=True, config={}
        )

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        return seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    def _clear(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.all().delete()

    def test_a_created_record_only_lane_carries_the_marker(self):
        from apps.orchestration.seeding import RECORD_ONLY, SEED_SHAPE_KEY

        self._clear()
        self._seed()

        for name in ("catch-all", "cluster-nodes", "resolved-all-clear"):
            self.assertEqual(self._lane(name).tags.get(SEED_SHAPE_KEY), RECORD_ONLY, name)

    def test_a_delivering_seed_marks_nothing(self):
        """Nothing was taken away, so no lane carries a claim to restore."""
        from apps.orchestration.seeding import SEED_SHAPE_KEY

        self._clear()
        self._channel()
        self._seed()

        for name in ("catch-all", "cluster-nodes", "resolved-all-clear"):
            self.assertNotIn(SEED_SHAPE_KEY, self._lane(name).tags, name)

    def test_a_repaired_record_only_lane_carries_the_marker(self):
        """Repair is the path a real install takes: 0012/0014/0016 ran first."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import RECORD_ONLY, SEED_SHAPE_KEY

        self._clear()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=["check", "analyze", "notify"], priority=1000
        )

        self._seed()

        self.assertEqual(self._lane("catch-all").tags.get(SEED_SHAPE_KEY), RECORD_ONLY)

    def test_junk_in_tags_does_not_break_the_marker(self):
        """``tags`` is a JSONField: a fixture or shell edit can persist a non-dict."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import RECORD_ONLY, SEED_SHAPE_KEY

        self._clear()
        PipelineDefinition.objects.create(
            name="catch-all",
            match=[],
            stages=["check", "analyze", "notify"],
            priority=1000,
            tags=["not", "a", "dict"],
        )

        self._seed()

        self.assertEqual(self._lane("catch-all").tags.get(SEED_SHAPE_KEY), RECORD_ONLY)


class RepairNeverResurrectsTests(TestCase):
    """Shaping may turn a lane OFF; it may never turn one ON.

    A lane that cannot deliver is safely quiet, so switching it off is a repair. An
    operator who disabled a lane made a decision, and ``migrate`` running again is
    not the moment to overrule it — the definition is hub-side truth and only an
    operator changes what it says.
    """

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        return seed_routing_table(PipelineDefinition, NotificationChannel)

    def _prior_row(self, name, stages, priority, is_active=True):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.all().delete()
        return PipelineDefinition.objects.create(
            name=name, match=[], stages=stages, priority=priority, is_active=is_active
        )

    def test_a_disabled_lane_stays_disabled_through_a_repair(self):
        from apps.notify.models import NotificationChannel

        NotificationChannel.objects.create(name="ops", driver="generic", is_active=True, config={})
        lane = self._prior_row("catch-all", ["check", "analyze", "notify"], 1000, is_active=False)

        self._seed()

        lane.refresh_from_db()
        self.assertFalse(lane.is_active)

    def test_a_disabled_lane_stays_disabled_on_a_channel_less_hub(self):
        lane = self._prior_row("catch-all", ["check", "analyze", "notify"], 1000, is_active=False)

        self._seed()

        lane.refresh_from_db()
        self.assertFalse(lane.is_active)
        self.assertEqual(lane.stages, ["check", "analyze"])


class EnableDeliveryProvenanceTests(TestCase):
    """Only the lanes the seed itself shaped are restored, and only once."""

    def _channel(self):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name="ops", driver="generic", is_active=True, config={}
        )

    def _record_only_seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        PipelineDefinition.objects.all().delete()
        seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    def test_a_lane_the_seed_never_shaped_is_left_alone(self):
        """Same stages the seed would have written, but the seed did not write them.

        Shape is not provenance: an operator whose catch-all records by choice must
        not start delivering because a channel appeared.
        """
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        PipelineDefinition.objects.all().delete()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=["check", "analyze"], priority=1000
        )
        channel = self._channel()

        restored = enable_delivery(PipelineDefinition, channel)

        self.assertEqual(restored, 0)
        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])

    def test_restoring_clears_the_marker(self):
        """The claim is spent: the lane is no longer the shape the seed wrote."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import SEED_SHAPE_KEY, enable_delivery

        self._record_only_seed()
        channel = self._channel()

        enable_delivery(PipelineDefinition, channel)

        self.assertNotIn(SEED_SHAPE_KEY, self._lane("catch-all").tags)

    def test_an_operator_disabled_lane_is_not_re_enabled(self):
        """The seed only ever switched off ``resolved-all-clear``; the rest is theirs."""
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import enable_delivery

        self._record_only_seed()
        lane = self._lane("catch-all")
        lane.is_active = False
        lane.save(update_fields=["is_active"])
        channel = self._channel()

        enable_delivery(PipelineDefinition, channel)

        lane.refresh_from_db()
        self.assertFalse(lane.is_active)
        self.assertIsNone(lane.channel_id)
