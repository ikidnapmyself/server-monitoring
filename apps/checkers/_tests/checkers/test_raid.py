"""Tests for the RAID /proc/mdstat parser."""

from django.test import TestCase

from apps.checkers.checkers import CHECKER_REGISTRY, RaidChecker
from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.raid import ArrayState, _parse_level, _read_mdstat, parse_mdstat

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

# A healthy array (`[2/2] [UU]`) undergoing a routine scrub/check. Counts
# are healthy; a scrub must NOT raise severity.
SCRUB = """\
md0 : active raid1 sdb1[1] sda1[0]
      1953382464 blocks super 1.2 [2/2] [UU]
      [==>..................]  check = 12.3% (240/1953) finish=5.0min speed=100000K/sec

unused devices: <none>
"""

# A genuinely degraded array (`sdd1[3](F)`, `[3/2] [UU_]`) that also
# happens to be mid-scrub. The scrub must not mask the failure.
DEGRADED_SCRUB = """\
md1 : active raid5 sdd1[3](F) sdc1[1] sde1[0]
      3906764800 blocks super 1.2 [3/2] [UU_]
      [========>............]  check = 40.0% (1562/3906) finish=5.0min speed=100000K/sec

unused devices: <none>
"""

# A degraded array (`[2/1] [U_]`) with a faulty device that is actively
# recovering. Real recovery suppresses CRITICAL -> WARNING.
FAULTY_RECOVERING = """\
md0 : active raid1 sdb1[2](F) sda1[0]
      1953382464 blocks super 1.2 [2/1] [U_]
      [======>..............]  recovery = 30.0% (585/1953) finish=5.0min speed=100000K/sec

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


class ParseMdstatOperationClassTests(TestCase):
    def _array_for_op(self, op):
        text = (
            "md0 : active raid1 sdb1[1] sda1[0]\n"
            "      1953382464 blocks super 1.2 [2/2] [UU]\n"
            f"      [==>..................]  {op} = 40.0% (240/1953) finish=5min\n"
        )
        arrays = parse_mdstat(text)
        self.assertEqual(len(arrays), 1)
        return arrays[0]

    def test_check_is_scrub_not_rebuild(self):
        array = self._array_for_op("check")
        self.assertTrue(array.scrubbing)
        self.assertFalse(array.rebuilding)
        self.assertEqual(array.resync_percent, 40.0)

    def test_repair_is_scrub_not_rebuild(self):
        array = self._array_for_op("repair")
        self.assertTrue(array.scrubbing)
        self.assertFalse(array.rebuilding)
        self.assertEqual(array.resync_percent, 40.0)

    def test_recovery_is_rebuild_not_scrub(self):
        array = self._array_for_op("recovery")
        self.assertTrue(array.rebuilding)
        self.assertFalse(array.scrubbing)

    def test_resync_is_rebuild_not_scrub(self):
        array = self._array_for_op("resync")
        self.assertTrue(array.rebuilding)
        self.assertFalse(array.scrubbing)

    def test_reshape_is_rebuild_not_scrub(self):
        array = self._array_for_op("reshape")
        self.assertTrue(array.rebuilding)
        self.assertFalse(array.scrubbing)


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


class ParseMdstatDeviceNameTests(TestCase):
    def test_named_array_is_not_skipped(self):
        # A named/partitionable array (md_home) must still be parsed, or a
        # degraded named array would silently report healthy.
        text = (
            "md_home : active raid1 sdb1[1] sda1[0](F)\n"
            "      1953382464 blocks super 1.2 [2/1] [U_]\n"
            "\nunused devices: <none>\n"
        )
        arrays = parse_mdstat(text)

        self.assertEqual(len(arrays), 1)
        self.assertEqual(arrays[0].name, "md_home")
        self.assertTrue(arrays[0].is_degraded())

    def test_hyphenated_member_device_is_captured(self):
        # Device-mapper members (dm-0) contain a hyphen; the faulty device
        # name must be captured in full, not truncated to "0".
        text = (
            "md1 : active raid1 dm-1[1] dm-0[0](F)\n"
            "      1953382464 blocks super 1.2 [2/1] [U_]\n"
            "\nunused devices: <none>\n"
        )
        arrays = parse_mdstat(text)

        self.assertEqual(arrays[0].failed, ["dm-0"])


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


class RaidCheckerTests(TestCase):
    def _patch(self, monkeypatch_target, value):
        # TestCase has no pytest monkeypatch; patch via setattr + addCleanup.
        import apps.checkers.checkers.raid as raid_mod

        original = getattr(raid_mod, monkeypatch_target)
        setattr(raid_mod, monkeypatch_target, value)
        self.addCleanup(setattr, raid_mod, monkeypatch_target, original)

    def _patch_platform(self, value):
        import apps.checkers.checkers.raid as raid_mod

        original = raid_mod.sys.platform
        raid_mod.sys.platform = value
        self.addCleanup(setattr, raid_mod.sys, "platform", original)

    def test_healthy_returns_ok(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: HEALTHY)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["array_count"], 1)
        self.assertEqual(result.metrics["degraded_arrays"], [])
        self.assertEqual(result.metrics["rebuilding_arrays"], [])
        self.assertEqual(result.metrics["arrays"][0]["name"], "md0")

    def test_degraded_not_rebuilding_returns_critical(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: DEGRADED)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertIn("md1", result.metrics["degraded_arrays"])
        self.assertIn("md1", result.message)
        self.assertEqual(result.metrics["rebuilding_arrays"], [])

    def test_rebuilding_returns_warning_not_critical(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: REBUILDING)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["rebuilding_arrays"], ["md0"])
        self.assertEqual(result.metrics["degraded_arrays"], [])
        self.assertIn("md0", result.message)

    def test_scrub_on_healthy_array_returns_ok(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: SCRUB)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["scrubbing_arrays"], ["md0"])
        self.assertEqual(result.metrics["rebuilding_arrays"], [])
        self.assertEqual(result.metrics["degraded_arrays"], [])

    def test_scrub_does_not_mask_degraded_array(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: DEGRADED_SCRUB)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertIn("md1", result.metrics["degraded_arrays"])
        self.assertIn("md1", result.metrics["scrubbing_arrays"])
        self.assertEqual(result.metrics["rebuilding_arrays"], [])

    def test_recovery_suppresses_critical_even_with_faulty_device(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: FAULTY_RECOVERING)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["rebuilding_arrays"], ["md0"])
        self.assertEqual(result.metrics["degraded_arrays"], [])
        self.assertEqual(result.metrics["scrubbing_arrays"], [])

    def test_empty_mdstat_returns_ok(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: EMPTY)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.message, "No software RAID arrays")
        self.assertEqual(result.metrics["array_count"], 0)

    def test_non_linux_returns_ok_skip(self):
        self._patch_platform("darwin")

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["array_count"], 0)

    def test_missing_mdstat_returns_ok_skip(self):
        self._patch_platform("linux")
        self._patch("_read_mdstat", lambda: None)

        result = RaidChecker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("mdstat unavailable", result.message)


class ReadMdstatTests(TestCase):
    def test_oserror_returns_none(self):
        import apps.checkers.checkers.raid as raid_mod

        class _Unreadable:
            def read_text(self):
                raise OSError("boom")

        original = raid_mod.MDSTAT
        raid_mod.MDSTAT = _Unreadable()
        self.addCleanup(setattr, raid_mod, "MDSTAT", original)

        self.assertIsNone(_read_mdstat())


class RaidRegistryTests(TestCase):
    def test_registry_maps_raid_to_checker(self):
        self.assertIs(CHECKER_REGISTRY["raid"], RaidChecker)
