import pytest
from django.urls import reverse

from apps.alerts.models import Alert, AlertHistory, Node
from apps.alerts.reeval_existing import ReevalScope, scope_digest
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _url(alert):
    return reverse("console:alert-reeval", args=[alert.pk])


def _digest(alert):
    return scope_digest(ReevalScope.for_alert(alert))


def test_preview_writes_nothing(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Would " in body
    assert 'name="digest"' in body
    assert "<title>Re-evaluate " in body
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_skip_row_links_the_policy_editor(client, superuser, reevaluable_alert):
    Node.objects.filter(pk=reevaluable_alert.node_id).update(config={})
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert 'id="reeval-row-1-action"' in body
    assert 'id="reeval-apply"' not in body


def test_htmx_gets_the_preview_without_the_layout(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert), **HTMX).content.decode()
    assert "Would " in body and "<title>" not in body


def test_view_only_user_sees_the_preview_without_a_button(
    client, view_alert_user, reevaluable_alert
):
    client.force_login(view_alert_user)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Would " in body and 'id="reeval-apply"' not in body


def test_nothing_to_change_offers_no_button(client, superuser, reevaluable_alert):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="warning")
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert 'id="reeval-apply"' not in body


def test_apply_writes_history_enqueues_and_redirects(client, superuser, reevaluable_alert):
    client.force_login(superuser)

    response = client.post(_url(reevaluable_alert), {"digest": _digest(reevaluable_alert)})

    assert response.status_code == 303
    assert response["Location"] == reverse("console:alert", args=[reevaluable_alert.pk])
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()
    assert PipelineRun.objects.filter(incident=reevaluable_alert.incident).exists()
    page = client.get(response["Location"]).content.decode()
    assert "Resolved 0, changed severity on 1" in page


def test_a_stale_digest_refuses_and_writes_nothing(client, superuser, reevaluable_alert):
    client.force_login(superuser)

    response = client.post(_url(reevaluable_alert), {"digest": "stale"})

    assert response.status_code == 409
    assert "This alert changed since you looked." in response.content.decode()
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_post_without_a_digest_is_stale(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    assert client.post(_url(reevaluable_alert)).status_code == 409
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_post_is_a_no_op(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    digest = _digest(reevaluable_alert)
    client.post(_url(reevaluable_alert), {"digest": digest})
    written = AlertHistory.objects.count()

    assert client.post(_url(reevaluable_alert), {"digest": digest}).status_code == 409
    assert AlertHistory.objects.count() == written


def test_post_needs_change_permission(client, view_alert_user, reevaluable_alert):
    client.force_login(view_alert_user)
    assert client.post(_url(reevaluable_alert), {"digest": "x"}).status_code == 403


def test_a_refused_post_lands_on_the_preview_after_login(client, reevaluable_alert):
    response = client.post(_url(reevaluable_alert), {"digest": "x"})
    assert response.status_code == 302
    assert response["Location"].endswith("?next=" + _url(reevaluable_alert))


def test_missing_alert_is_404(client, superuser):
    client.force_login(superuser)
    assert client.get(reverse("console:alert-reeval", args=[999])).status_code == 404


def test_posting_to_a_missing_alert_is_404(client, superuser):
    client.force_login(superuser)
    assert client.post(reverse("console:alert-reeval", args=[999])).status_code == 404


def test_a_resolved_alert_warns_before_reopening(client, superuser, reevaluable_alert):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(status="resolved")
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Applying reopens it and its incident." in body
