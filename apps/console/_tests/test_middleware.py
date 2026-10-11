import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.console.middleware import CSP

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_staff_page_carries_the_strict_csp(client, staff):
    resp = client.get(reverse("console:overview"))

    policy = resp["Content-Security-Policy"]
    assert policy == CSP
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "unsafe" not in policy


@pytest.mark.django_db
def test_staff_page_is_never_cached_and_varies_on_htmx(client, staff):
    resp = client.get(reverse("console:overview"))

    assert "no-store" in resp["Cache-Control"]
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("signed_in", "extra", "status"),
    [
        (False, {}, 302),
        (False, HTMX, 204),
        (True, {}, 403),
    ],
    ids=["anonymous", "anonymous-htmx", "non-staff"],
)
def test_refusals_carry_the_headers(client, signed_in, extra, status):
    if signed_in:
        client.force_login(get_user_model().objects.create_user("visitor", password="x"))

    resp = client.get(reverse("console:overview"), **extra)

    assert resp.status_code == status
    assert resp["Content-Security-Policy"] == CSP
    assert "no-store" in resp["Cache-Control"]
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
def test_a_response_no_console_view_returned_still_carries_the_headers(client, staff):
    resp = client.get("/console/does-not-exist/")

    assert resp.status_code == 404
    assert resp["Content-Security-Policy"] == CSP
    assert "no-store" in resp["Cache-Control"]
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
def test_paths_outside_the_console_are_left_alone(client):
    resp = client.get(reverse("admin:login"))

    assert resp.status_code == 200
    assert resp.get("Content-Security-Policy") != CSP
    assert "HX-Request" not in resp.get("Vary", "")


def test_the_middleware_wraps_everything_but_the_security_middleware():
    names = settings.MIDDLEWARE
    security = names.index("django.middleware.security.SecurityMiddleware")

    assert names[security + 1] == "apps.console.middleware.ConsoleHeadersMiddleware"
