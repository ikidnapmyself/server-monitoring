import pytest

from apps.console.formatting import humanize_duration


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "0s"),
        (0, "0s"),
        (0.0004, "0ms"),
        (0.85, "850ms"),
        (0.9996, "999ms"),
        (1, "1s"),
        (59.9, "59s"),
        (60, "1m 00s"),
        (252, "4m 12s"),
        (3599, "59m 59s"),
        (3600, "1h 00m"),
        (11100, "3h 05m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (187200, "2d 4h"),
    ],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected
