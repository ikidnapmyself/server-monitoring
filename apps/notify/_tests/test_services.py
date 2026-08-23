"""Tests for `apps.notify.services.NotifySelector`."""

from django.test import TestCase


class DefaultChannelIsOptInTests(TestCase):
    """The single-channel default is for callers who ARE the operator.

    Picking "the first active channel ordered by name" is the right answer for
    `test_notify` with no argument, and a silent misroute for a pipeline lane whose
    channel is unset — a critical alert landing in #aaa-general because of its name.
    So the caller opts in rather than the selector assuming.
    """

    def setUp(self):
        from apps.notify.models import NotificationChannel

        NotificationChannel.objects.create(
            name="aaa-first", driver="generic", is_active=True, config={}
        )

    def test_no_provider_and_no_opt_in_selects_no_channel(self):
        from apps.notify.services import NotifySelector

        _, _, _, _, channel_obj, _ = NotifySelector.resolve(None, {})

        self.assertIsNone(channel_obj)

    def test_no_provider_with_opt_in_picks_the_active_channel(self):
        from apps.notify.services import NotifySelector

        _, _, label, _, channel_obj, _ = NotifySelector.resolve(
            None, {}, allow_default_channel=True
        )

        self.assertIsNotNone(channel_obj)
        self.assertEqual(label, "aaa-first")

    def test_a_named_channel_still_wins_without_opt_in(self):
        """Opt-in governs only the no-argument case."""
        from apps.notify.services import NotifySelector

        _, _, label, _, channel_obj, _ = NotifySelector.resolve("aaa-first", {})

        self.assertIsNotNone(channel_obj)
        self.assertEqual(label, "aaa-first")
