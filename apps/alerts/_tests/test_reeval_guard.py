import pytest

from apps.alerts._tests.fixtures import reevaluable_alert  # noqa: F401
from apps.alerts.models import Alert, AlertHistory
from apps.alerts.reeval_existing import ReevalScope, StaleReeval, apply_reeval, scope_digest

pytestmark = pytest.mark.django_db


def test_digest_is_stable_until_the_alert_changes(reevaluable_alert):  # noqa: F811
    scope = ReevalScope.for_alert(reevaluable_alert)
    before = scope_digest(scope)
    assert scope_digest(scope) == before

    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="info")

    assert scope_digest(scope) != before


def test_apply_with_a_matching_digest_writes(reevaluable_alert):  # noqa: F811
    scope = ReevalScope.for_alert(reevaluable_alert)
    report = apply_reeval(scope, expected=scope_digest(scope))
    assert report.changes
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_submit_is_refused_and_writes_nothing(reevaluable_alert):  # noqa: F811
    scope = ReevalScope.for_alert(reevaluable_alert)
    digest = scope_digest(scope)
    apply_reeval(scope, expected=digest)
    written = AlertHistory.objects.count()

    with pytest.raises(StaleReeval):
        apply_reeval(scope, expected=digest)

    assert AlertHistory.objects.count() == written


def test_no_expected_digest_skips_the_check(reevaluable_alert):  # noqa: F811
    scope = ReevalScope.for_alert(reevaluable_alert)
    assert apply_reeval(scope).changes
