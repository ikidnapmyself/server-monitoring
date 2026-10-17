import pytest

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.alerts.reeval_existing import ReevalScope, StaleReeval, apply_reeval, scope_digest

pytestmark = pytest.mark.django_db


def test_digest_is_stable_until_the_alert_changes(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    before = scope_digest(scope)
    assert scope_digest(scope) == before

    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="info")

    assert scope_digest(scope) != before


def test_apply_with_a_matching_digest_writes(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    report = apply_reeval(scope, expected=scope_digest(scope))
    assert report.changes
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_submit_is_refused_and_writes_nothing(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    digest = scope_digest(scope)
    apply_reeval(scope, expected=digest)
    written = AlertHistory.objects.count()

    with pytest.raises(StaleReeval):
        apply_reeval(scope, expected=digest)

    assert AlertHistory.objects.count() == written


def test_no_expected_digest_skips_the_check(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    assert apply_reeval(scope).changes


def _refused_after(alert, change):
    digest = scope_digest(ReevalScope.for_alert(alert))
    change()
    with pytest.raises(StaleReeval):
        apply_reeval(ReevalScope.for_alert(alert), expected=digest)
    alert.refresh_from_db()
    assert alert.severity == "critical"
    assert not AlertHistory.objects.filter(alert=alert).exists()


def test_a_policy_edit_after_the_preview_is_refused(reevaluable_alert):
    _refused_after(
        reevaluable_alert,
        lambda: Node.objects.filter(pk=reevaluable_alert.node_id).update(
            config={"cpu": {"warning_threshold": 50, "critical_threshold": 60}}
        ),
    )


def test_an_incident_status_change_after_the_preview_is_refused(reevaluable_alert):
    _refused_after(
        reevaluable_alert,
        lambda: Incident.objects.filter(pk=reevaluable_alert.incident_id).update(
            status="acknowledged"
        ),
    )


def test_the_digest_does_not_depend_on_config_key_order(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    before = scope_digest(scope)
    Node.objects.filter(pk=reevaluable_alert.node_id).update(
        config={"cpu": {"critical_threshold": 99, "warning_threshold": 80}}
    )
    assert scope_digest(ReevalScope.for_alert(reevaluable_alert)) == before


def test_a_scope_without_a_node_still_has_a_digest(reevaluable_alert):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(labels={"checker": "cpu"})
    reevaluable_alert.refresh_from_db()
    scope = ReevalScope.for_alert(reevaluable_alert)
    assert scope.node is None
    assert scope_digest(scope) == scope_digest(ReevalScope.for_alert(reevaluable_alert))
