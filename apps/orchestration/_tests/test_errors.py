"""Tests for the shared routing-gap failure (apps/orchestration/errors.py)."""

from django.test import SimpleTestCase


class RoutingGapTests(SimpleTestCase):
    """One construct for 'the routing table does not say where this goes'.

    no_route (no lane matched) and no_channel (the lane names no active channel)
    differ only in which half is missing. Both are terminal until an operator edits
    a row, so both must be non-retryable — and that must be stated once.
    """

    def test_a_routing_gap_is_never_retryable(self):
        from apps.orchestration.errors import routing_gap

        error = routing_gap("routing", "no_route", "no active pipeline matched this alert")

        self.assertFalse(error.retryable)

    def test_the_code_leads_the_message(self):
        """Operators and log searches key on the code, so it must be the prefix."""
        from apps.orchestration.errors import routing_gap

        error = routing_gap("notify", "no_channel", "the matched lane names no active channel")

        self.assertTrue(error.errors[0].startswith("no_channel: "))

    def test_it_carries_the_stage_it_was_raised_for(self):
        from apps.orchestration.errors import routing_gap

        self.assertEqual(routing_gap("notify", "no_channel", "x").stage, "notify")
