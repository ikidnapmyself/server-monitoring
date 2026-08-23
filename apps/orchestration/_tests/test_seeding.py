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

        self.assertEqual(report["created"], 4)
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

    def test_hub_self_check_never_gets_a_channel(self):
        """It lists no stages, so it never delivers and needs nothing bound."""
        self._channel()

        self._seed()

        self.assertIsNone(self._lane("hub-self-check").channel_id)

    def test_hub_self_check_stays_active_with_no_stages(self):
        """Record-and-correlate is its designed shape, not an accident of the seed."""
        self._seed()

        lane = self._lane("hub-self-check")
        self.assertEqual(lane.stages, [])
        self.assertTrue(lane.is_active)
