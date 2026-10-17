import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.alerts.models import Incident

HTMX = {"HTTP_HX_REQUEST": "true"}
REFUSAL = "Permission to view this page required."


def _pages():
    incident = Incident.objects.create(severity="warning")
    return [
        reverse("console:overview"),
        reverse("console:incidents"),
        reverse("console:incident", args=[incident.pk]),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_staff_without_the_view_permission_is_refused_every_read(client, bare_staff, headers):
    for url in _pages():
        resp = client.get(url, **headers)

        assert resp.status_code == 403, url
        assert resp.content.decode() == REFUSAL


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_staff_with_the_view_permission_reads_every_page(client, staff, headers):
    for url in _pages():
        assert client.get(url, **headers).status_code == 200, url


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_change_permission_alone_reads_every_page_as_the_admin_allows(client, bare_staff, headers):
    bare_staff.user_permissions.add(
        Permission.objects.get(content_type__app_label="alerts", codename="change_incident")
    )

    for url in _pages():
        assert client.get(url, **headers).status_code == 200, url


@pytest.mark.django_db
def test_a_missing_incident_is_refused_before_it_is_looked_up(client, bare_staff):
    resp = client.get(reverse("console:incident", args=[999999]))

    assert (resp.status_code, resp.content.decode()) == (403, REFUSAL)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["console:incident-rerun", "console:incident-action"], ids=["rerun", "action"]
)
def test_an_action_needs_the_view_permission_first(client, bare_staff, name):
    incident = Incident.objects.create(severity="warning")
    args = [incident.pk] if name == "console:incident-rerun" else [incident.pk, "acknowledge"]

    resp = client.post(reverse(name, args=args))

    assert (resp.status_code, resp.content.decode()) == (403, REFUSAL)
    incident.refresh_from_db()
    assert incident.status == "open"
