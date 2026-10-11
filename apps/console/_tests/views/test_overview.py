import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.console.views.base import CSP
from apps.console.views.overview import OverviewView

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_non_staff_is_refused(client):
    user = get_user_model().objects.create_user("visitor", password="x")
    client.force_login(user)

    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 403
    assert "Staff access required." in resp.content.decode()


def test_inactive_staff_is_refused_by_the_view_itself():
    request = RequestFactory().get("/console/")
    request.user = get_user_model()(username="gone", is_staff=True, is_active=False)

    resp = OverviewView.as_view()(request)

    assert resp.status_code == 403
    assert "Staff access required." in resp.content.decode()


@pytest.mark.django_db
def test_staff_gets_the_page(client, staff):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 200
    assert "<html" in resp.content.decode()


@pytest.mark.django_db
def test_root_redirects_to_the_console(client):
    resp = client.get("/")

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_every_response_carries_the_csp(client, staff):
    resp = client.get(reverse("console:overview"))

    assert resp["Content-Security-Policy"] == CSP
    assert "script-src 'self'" in CSP
    assert "style-src 'self'" in CSP
    assert "unsafe" not in CSP


@pytest.mark.django_db
def test_refusals_carry_the_csp_too(client):
    resp = client.get(reverse("console:overview"))

    assert resp["Content-Security-Policy"] == CSP


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, staff):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 200
    assert "<html" not in resp.content.decode()
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
def test_anonymous_htmx_request_is_told_to_do_a_full_redirect(client):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_staff_response_is_never_cached(client, staff):
    resp = client.get(reverse("console:overview"))

    assert "no-store" in resp["Cache-Control"]


@pytest.mark.django_db
def test_refusals_are_never_cached(client):
    resp = client.get(reverse("console:overview"))

    assert "no-store" in resp["Cache-Control"]
