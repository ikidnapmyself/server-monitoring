import re
from unittest import mock

import pytest
from django.db import OperationalError
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
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Would " in body
    assert 'name="digest"' in body
    assert "<title>Re-evaluate " in body
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_the_preview_states_what_an_apply_queues(client, superuser, reevaluable_alert):
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Applying queues 1 pipeline run." in body


def test_a_skip_row_links_the_policy_editor(client, superuser, reevaluable_alert):
    Node.objects.filter(pk=reevaluable_alert.node_id).update(config={})
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert 'id="reeval-row-1-action"' in body
    assert 'id="reeval-apply"' not in body


def test_htmx_gets_the_preview_without_the_layout(client, superuser, reevaluable_alert):
    body = client.get(_url(reevaluable_alert), **HTMX).content.decode()
    assert "Would " in body and "<title>" not in body


def test_nothing_to_change_offers_no_button(client, superuser, reevaluable_alert):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="warning")
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert 'id="reeval-apply"' not in body


def test_a_crafted_post_with_nothing_to_change_applies_nothing(
    client, superuser, reevaluable_alert
):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="warning")
    reevaluable_alert.refresh_from_db()

    resp = client.post(_url(reevaluable_alert), {"digest": _digest(reevaluable_alert)})

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "Nothing here needs re-evaluating, so nothing was applied." in body
    assert "Resolved 0" not in body
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()
    assert not PipelineRun.objects.exists()
    page = client.get(reverse("console:alert", args=[reevaluable_alert.pk]))
    assert "Resolved 0" not in page.content.decode()


def test_apply_writes_history_enqueues_and_redirects(client, superuser, reevaluable_alert):

    response = client.post(_url(reevaluable_alert), {"digest": _digest(reevaluable_alert)})

    assert response.status_code == 303
    assert response["Location"] == reverse("console:alert", args=[reevaluable_alert.pk])
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()
    assert PipelineRun.objects.filter(incident=reevaluable_alert.incident).exists()
    page = client.get(response["Location"]).content.decode()
    assert "Resolved 0, changed severity on 1" in page


def test_a_stale_digest_refuses_and_writes_nothing(client, superuser, reevaluable_alert):

    response = client.post(_url(reevaluable_alert), {"digest": "stale"})

    assert response.status_code == 409
    assert "This alert changed since you looked." in response.content.decode()
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_locked_database_answers_like_a_stale_digest(client, superuser, reevaluable_alert):

    with mock.patch(
        "apps.console.views.alert_reeval.apply_reeval",
        side_effect=OperationalError("database is locked"),
    ):
        response = client.post(_url(reevaluable_alert), {"digest": _digest(reevaluable_alert)})

    assert response.status_code == 409
    assert "This alert changed since you looked." in response.content.decode()
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_the_apply_form_disables_itself_on_submit(client, superuser, reevaluable_alert):
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert re.search(r'<form method="post" action="[^"]*" data-disable-on-submit>', body)


def test_a_post_without_a_digest_is_stale(client, superuser, reevaluable_alert):
    assert client.post(_url(reevaluable_alert)).status_code == 409
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_post_is_a_no_op(client, superuser, reevaluable_alert):
    digest = _digest(reevaluable_alert)
    client.post(_url(reevaluable_alert), {"digest": digest})
    written = AlertHistory.objects.count()

    assert client.post(_url(reevaluable_alert), {"digest": digest}).status_code == 409
    assert AlertHistory.objects.count() == written


def test_a_refused_post_lands_on_the_preview_after_login(client, reevaluable_alert):
    response = client.post(_url(reevaluable_alert), {"digest": "x"})
    assert response.status_code == 302
    assert response["Location"].endswith("?next=" + _url(reevaluable_alert))


def test_missing_alert_is_404(client, superuser):
    assert client.get(reverse("console:alert-reeval", args=[999])).status_code == 404


def test_posting_to_a_missing_alert_is_404(client, superuser):
    assert client.post(reverse("console:alert-reeval", args=[999])).status_code == 404


def test_a_resolved_alert_warns_before_reopening(client, superuser, reevaluable_alert):
    Alert.objects.filter(pk=reevaluable_alert.pk).update(status="resolved")
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Applying reopens it and its incident." in body
