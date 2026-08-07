"""Tests for the shared hwmon sensor helper."""

from collections import namedtuple
from unittest import mock

from django.test import TestCase

from apps.checkers.checkers._sensors import TempReading, parse_temps, read_temps

shwtemp = namedtuple("shwtemp", ["label", "current", "high", "critical"])


def _raw():
    return {
        "drivetemp": [shwtemp("sda", 58.0, 60.0, 65.0), shwtemp("sdb", 40.0, 60.0, 65.0)],
        "nvme": [shwtemp("Composite", 44.0, None, 84.0)],
        "coretemp": [shwtemp("Package id 0", 70.0, 100.0, 100.0)],  # not a disk chip
    }


class ParseTempsTests(TestCase):
    def test_selects_only_allowlisted_chips(self):
        readings = parse_temps(_raw(), {"drivetemp", "nvme"})
        self.assertEqual({r.label for r in readings}, {"sda", "sdb", "Composite"})
        self.assertTrue(all(isinstance(r, TempReading) for r in readings))
        sda = next(r for r in readings if r.label == "sda")
        self.assertEqual(sda.chip, "drivetemp")
        self.assertEqual(sda.current, 58.0)
        self.assertEqual(sda.critical, 65.0)

    def test_drops_none_and_nonpositive_current(self):
        raw = {
            "drivetemp": [
                shwtemp("sda", None, None, None),
                shwtemp("sdb", 0.0, None, None),
                shwtemp("sdc", 55.0, None, None),
            ]
        }
        readings = parse_temps(raw, {"drivetemp"})
        self.assertEqual([r.label for r in readings], ["sdc"])

    def test_empty_label_falls_back_to_chip(self):
        raw = {"nvme": [shwtemp("", 44.0, None, None)]}
        self.assertEqual(parse_temps(raw, {"nvme"})[0].label, "nvme")

    def test_empty_when_no_matching_chip(self):
        self.assertEqual(parse_temps({"coretemp": []}, {"drivetemp", "nvme"}), [])


class ReadTempsTests(TestCase):
    def test_returns_empty_when_function_absent(self):
        with mock.patch("apps.checkers.checkers._sensors.psutil") as mock_psutil:
            # Simulate a platform where sensors_temperatures is not defined.
            del mock_psutil.sensors_temperatures
            self.assertEqual(read_temps({"drivetemp"}), [])

    def test_delegates_to_parse_temps(self):
        with mock.patch(
            "apps.checkers.checkers._sensors.psutil.sensors_temperatures",
            return_value=_raw(),
            create=True,
        ):
            readings = read_temps({"drivetemp"})
        self.assertEqual({r.label for r in readings}, {"sda", "sdb"})
