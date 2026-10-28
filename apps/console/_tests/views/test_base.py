import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.alerts.models import Incident, Node
from apps.console.views import OverviewView
from apps.console.views.base import admits
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
HEADING = "Superusers only"


def _pages():
    incident = Incident.objects.create(severity="warning")
    return [
        reverse("console:overview"),
        reverse("console:incidents"),
        reverse("console:incident", args=[incident.pk]),
    ]


def _refused(resp):
    body = resp.content.decode()
    logout = reverse("admin:logout")
    assert resp.status_code == 403
    assert resp.templates[0].name == "console/not_superuser.html"
    assert f">{HEADING}</h1>" in body
    assert 'class="c-unav"' not in body
    assert (
        f'<form id="not-superuser-sign-out" class="c-section" method="post" action="{logout}">'
        in body
    )
    assert body.count(f'action="{logout}"') == 2


@pytest.mark.parametrize(
    ("fields", "admitted"),
    [
        ({"is_superuser": True, "is_active": True}, True),
        ({"is_superuser": True, "is_active": False}, False),
        ({"is_staff": True, "is_active": True}, False),
        ({"is_active": True}, False),
    ],
    ids=["superuser", "inactive-superuser", "staff", "plain"],
)
def test_admits_only_an_active_superuser(fields, admitted):
    assert admits(get_user_model()(username="u", **fields)) is admitted


def test_admits_no_one_signed_out():
    assert admits(AnonymousUser()) is False


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_a_superuser_reads_every_page(client, superuser, headers):
    for url in _pages():
        assert client.get(url, **headers).status_code == 200, url


@pytest.mark.django_db
def test_active_staff_who_arent_superusers_are_refused_every_page(client, active_staff):
    for url in _pages():
        _refused(client.get(url))


@pytest.mark.django_db
def test_a_refused_htmx_request_reloads_into_the_refusal_page(client, active_staff):
    for url in _pages():
        resp = client.get(url, **HTMX)

        assert resp.status_code == 403, url
        assert resp["HX-Refresh"] == "true"
        assert resp.content == b""


@pytest.mark.django_db
def test_a_plain_user_is_refused(client):
    client.force_login(get_user_model().objects.create_user("visitor", password="x"))

    _refused(client.get(reverse("console:overview")))


@pytest.mark.django_db
def test_an_inactive_superuser_is_refused(rf):
    request = rf.get(reverse("console:overview"))
    request.user = get_user_model().objects.create_superuser("gone", password="x", is_active=False)

    resp = OverviewView.as_view()(request)

    assert resp.status_code == 403
    assert HEADING in resp.content.decode()


@pytest.mark.django_db
def test_an_inactive_superusers_session_is_sent_to_the_login(client):
    user = get_user_model().objects.create_superuser("gone", password="x")
    client.force_login(user)
    get_user_model().objects.filter(pk=user.pk).update(is_active=False)

    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_a_signed_out_page_request_is_sent_to_the_login(client):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_a_missing_incident_is_refused_before_it_is_looked_up(client, active_staff):
    _refused(client.get(reverse("console:incident", args=[999999])))


def _posts():
    incident = Incident.objects.create(severity="warning")
    node = Node.objects.create(instance_id="i-web", hostname="web")
    run = PipelineRun.objects.create(
        run_id=str(uuid.uuid4()), trace_id=str(uuid.uuid4()), status="failed"
    )
    return [
        reverse("console:incident-action", args=[incident.pk, "acknowledge"]),
        reverse("console:incident-rerun", args=[incident.pk]),
        reverse("console:run-action", args=[run.pk, "retry"]),
        reverse("console:inbox-drain", args=[run.pk]),
        reverse("console:inbox-drain-oldest"),
        reverse("console:inbox-reclaim"),
        reverse("console:alert-reeval", args=[1]),
        reverse("console:node-policy", args=[node.pk]),
        reverse("console:node-allow-ports", args=[node.pk]),
        reverse("console:node-reeval", args=[node.pk]),
        reverse("console:route-new"),
        reverse("console:channel-new"),
        reverse("console:provider-new"),
        reverse("console:key-new"),
    ]


@pytest.mark.django_db
def test_every_post_refuses_active_staff_and_changes_nothing(client, active_staff):
    for url in _posts():
        _refused(client.post(url))

    assert Incident.objects.get().status == "open"
    assert PipelineRun.objects.get().status == "failed"
