import re

import pytest

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from config.describing import Action

RAW = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b|[0-9a-f]{8}-[0-9a-f]{4}-|\{")


class Plain(BaseChecker):
    name = "plain"
    label = "Plain thing"
    problem = "Plain thing broken"

    def check(self) -> CheckResult:  # pragma: no cover
        raise NotImplementedError


class Described(Plain):
    def describe_result(self, status, metrics):
        return self._describe(status, f"Value {metrics['value']}", metrics, action=Action("Fix"))


class Broken(Plain):
    def describe_result(self, status, metrics):
        return self._describe(status, f"Value {metrics['missing']}", metrics)


def test_default_describe_falls_back_to_the_message():
    d = Plain().describe(CheckStatus.WARNING, {"load": 3}, "Load is 3")
    assert (d.headline, d.tone) == ("Load is 3", "warning")
    assert [f.label for f in d.facts] == ["Load"]


def test_default_describe_without_a_message_names_the_check():
    assert Plain().describe(CheckStatus.OK, {}).headline == "Plain thing: ok"


def test_skipped_reads_as_not_checked():
    d = Plain().describe(CheckStatus.SKIPPED, {}, "Skipped: not Linux")
    assert (d.headline, d.tone, d.action) == ("Plain thing not checked: not Linux", "muted", None)


def test_unknown_reads_as_could_not_check_with_the_error_as_detail():
    d = Plain().describe(CheckStatus.UNKNOWN, {}, "Check failed: boom")
    assert d.headline == "Plain thing could not be checked"
    assert d.detail == "Check failed: boom"
    assert d.action == Action("Run this check by hand on the host to see the error.")


def test_a_describer_result_wins_over_the_message():
    d = Described().describe(CheckStatus.CRITICAL, {"value": 7}, "raw")
    assert (d.headline, d.tone, d.action) == ("Value 7", "critical", Action("Fix"))


def test_metrics_a_describer_cannot_read_fall_back_to_the_message():
    assert Broken().describe(CheckStatus.CRITICAL, {}, "raw").headline == "raw"


def test_ok_results_carry_no_action():
    d = Plain()._describe(CheckStatus.OK, "Fine", {}, action=Action("Do it"))
    assert d.action is None


@pytest.mark.parametrize("name,cls", sorted(CHECKER_REGISTRY.items()))
def test_every_checker_has_a_label_and_a_problem(name, cls):
    assert cls.label and cls.problem and cls.label != cls.problem
    assert not RAW.search(cls.label) and not RAW.search(cls.problem)


@pytest.mark.parametrize("name,cls", sorted(CHECKER_REGISTRY.items()))
@pytest.mark.parametrize("status", list(CheckStatus))
def test_every_checker_describes_empty_metrics_in_words(name, cls, status):
    d = cls().describe(status, {}, "")
    assert d.headline[0].isalnum() and not RAW.search(d.headline)
