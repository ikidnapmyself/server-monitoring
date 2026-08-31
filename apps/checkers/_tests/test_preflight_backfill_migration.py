"""The 0003 data migration repairs preflight rows written with a blank id.

Exercised as a plain function rather than through the migration executor: the
migration body is the unit under test, and calling it directly keeps the test
fast and readable.
"""

import socket

from django.apps import apps as django_apps
from django.test import TestCase

from apps.checkers.migrations import (
    _0003_backfill_preflight_instance_id as backfill_module,
)
from apps.checkers.models import PreflightRun


class PreflightBackfillTests(TestCase):
    def test_blank_rows_get_this_machines_id(self):
        blank = PreflightRun.objects.create(instance_id="", overall_status="ok")
        backfill_module.backfill(django_apps, None)
        blank.refresh_from_db()
        # Asserted against the hostname directly, not ``local_instance_id()``:
        # the migration carries a frozen copy of that helper, so pinning it to
        # the live module would let the two drift apart unnoticed.
        self.assertEqual(blank.instance_id, socket.gethostname())

    def test_rows_that_already_name_a_machine_are_left_alone(self):
        named = PreflightRun.objects.create(instance_id="web-03", overall_status="ok")
        backfill_module.backfill(django_apps, None)
        named.refresh_from_db()
        self.assertEqual(named.instance_id, "web-03")
