import pytest

from apps.checkers.models import PreflightCheck, PreflightRun
from apps.checkers.preflight_describing import (
    NO_MESSAGE,
    describe_preflight_check,
    describe_preflight_run,
    preflight_headline,
)
from config.describing import Description


@pytest.mark.parametrize(
    ("status", "counts", "headline", "tone"),
    [
        ("ok", {"passed": 9}, "Preflight passed", "ok"),
        ("error", {"errors": 2, "warnings": 4}, "Preflight found 2 errors", "critical"),
        ("error", {"errors": 1}, "Preflight found 1 error", "critical"),
        ("warn", {"warnings": 3}, "Preflight found 3 warnings", "warning"),
        ("warn", {"warnings": 1}, "Preflight found 1 warning", "warning"),
        ("error", {}, "Preflight didn't pass", "critical"),
        ("warn", {}, "Preflight didn't pass", "warning"),
        ("odd", {}, "Preflight stored a result this hub can't read", "muted"),
    ],
)
def test_a_run_is_worded_by_its_status_and_counts(status, counts, headline, tone):
    run = PreflightRun(overall_status=status, **counts)

    assert describe_preflight_run(run) == Description(headline, tone)
    assert preflight_headline(status, run.warnings, run.errors) == headline


@pytest.mark.parametrize(
    ("level", "tone"),
    [
        ("error", "critical"),
        ("warn", "warning"),
        ("info", "info"),
        ("ok", "ok"),
        ("debug", "muted"),
    ],
)
def test_a_check_reads_its_message_and_hint_with_its_level_tone(level, tone):
    check = PreflightCheck(level=level, message=" .env is missing ", hint=" Copy .env.sample ")

    assert describe_preflight_check(check) == Description(
        ".env is missing", tone, detail="Copy .env.sample"
    )


def test_a_check_with_no_message_says_so():
    assert describe_preflight_check(PreflightCheck(level="ok")).headline == NO_MESSAGE
