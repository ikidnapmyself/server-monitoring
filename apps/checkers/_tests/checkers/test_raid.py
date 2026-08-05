"""Tests for the RAID /proc/mdstat parser."""

from django.test import TestCase

from apps.checkers.checkers.raid import ArrayState, _parse_level, parse_mdstat

HEALTHY = """\
Personalities : [raid1]
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""

DEGRADED = """\
md1 : active raid5 sdd1[3](F) sdc1[1] sde1[0]
      3906764800 blocks super 1.2 [3/2] [UU_]

unused devices: <none>
"""

REBUILDING = """\
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/1] [U_]
      [==========>..........]  recovery = 50.0% (976/1953) finish=10.0min speed=100000K/sec

unused devices: <none>
"""

REBUILDING_INTEGER = """\
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/1] [U_]
      [====================>]  recovery = 100% (1953/1953) finish=0.0min speed=100000K/sec

unused devices: <none>
"""

MULTI_ARRAY = """\
Personalities : [raid1] [raid5]
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/2] [UU]

md1 : active raid5 sdd1[3](F) sdc1[1] sde1[0]
      3906764800 blocks super 1.2 [3/2] [UU_]

unused devices: <none>
"""

EMPTY = """\
Personalities :
unused devices: <none>
"""

ACTIVE_NO_COUNTS = """\
md0 : active raid1 sda1[0]
      1953382464 blocks super 1.2

unused devices: <none>
"""

INACTIVE = """\
md2 : inactive sdf1[0](S)
      1953382464 blocks

unused devices: <none>
"""


class ParseMdstatHealthyTests(TestCase):
    def test_single_healthy_array(self):
        arrays = parse_mdstat(HEALTHY)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertEqual(array.name, "md0")
        self.assertEqual(array.level, "raid1")
        self.assertEqual(array.state, "active")
        self.assertEqual(array.active_devices, 2)
        self.assertEqual(array.total_devices, 2)
        self.assertEqual(array.failed, [])
        self.assertFalse(array.rebuilding)
        self.assertIsNone(array.resync_percent)
        self.assertFalse(array.is_degraded())


class ParseMdstatDegradedTests(TestCase):
    def test_degraded_array_with_failed_device(self):
        arrays = parse_mdstat(DEGRADED)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertEqual(array.name, "md1")
        self.assertEqual(array.level, "raid5")
        self.assertEqual(array.active_devices, 2)
        self.assertEqual(array.total_devices, 3)
        self.assertEqual(array.failed, ["sdd1"])
        self.assertTrue(array.is_degraded())


class ParseMdstatRebuildingTests(TestCase):
    def test_rebuilding_array_reports_progress(self):
        arrays = parse_mdstat(REBUILDING)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertTrue(array.rebuilding)
        self.assertEqual(array.resync_percent, 50.0)
        self.assertEqual(array.active_devices, 1)
        self.assertEqual(array.total_devices, 2)
        # A down slot during rebuild is not a failed device.
        self.assertEqual(array.failed, [])
        self.assertTrue(array.is_degraded())


class ParseMdstatIntegerPercentTests(TestCase):
    def test_integer_percent_is_parsed(self):
        arrays = parse_mdstat(REBUILDING_INTEGER)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertTrue(array.rebuilding)
        self.assertEqual(array.resync_percent, 100.0)


class ParseMdstatMultiArrayTests(TestCase):
    def test_two_arrays_parse_independently(self):
        arrays = parse_mdstat(MULTI_ARRAY)

        self.assertEqual(len(arrays), 2)
        healthy, degraded = arrays

        self.assertEqual(healthy.name, "md0")
        self.assertEqual(healthy.level, "raid1")
        self.assertEqual(healthy.failed, [])
        self.assertFalse(healthy.is_degraded())

        self.assertEqual(degraded.name, "md1")
        self.assertEqual(degraded.level, "raid5")
        self.assertEqual(degraded.failed, ["sdd1"])
        self.assertTrue(degraded.is_degraded())


class ParseMdstatEmptyTests(TestCase):
    def test_no_arrays(self):
        self.assertEqual(parse_mdstat(EMPTY), [])


class ParseMdstatInactiveTests(TestCase):
    def test_inactive_array_has_no_level(self):
        arrays = parse_mdstat(INACTIVE)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertEqual(array.name, "md2")
        self.assertEqual(array.state, "inactive")
        self.assertIsNone(array.level)
        self.assertTrue(array.is_degraded())


class ArrayStateTests(TestCase):
    def test_is_degraded_healthy(self):
        array = ArrayState(
            name="md0",
            state="active",
            level="raid1",
            active_devices=2,
            total_devices=2,
        )
        self.assertFalse(array.is_degraded())

    def test_is_degraded_active_with_unknown_counts(self):
        # Active, no failed devices, and counts unknown -> not degraded.
        array = ArrayState(name="md0", state="active")
        self.assertFalse(array.is_degraded())

    def test_active_array_without_counts_parses(self):
        arrays = parse_mdstat(ACTIVE_NO_COUNTS)

        self.assertEqual(len(arrays), 1)
        array = arrays[0]
        self.assertEqual(array.state, "active")
        self.assertIsNone(array.active_devices)
        self.assertIsNone(array.total_devices)
        self.assertFalse(array.is_degraded())


class ParseLevelTests(TestCase):
    def test_empty_remainder_yields_none(self):
        self.assertIsNone(_parse_level(""))
