from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.test_templates import FORBIDDEN

pytestmark = pytest.mark.django_db


@pytest.fixture
def url():
    return reverse("console:users")


def test_the_page_lives_at_users(url):
    assert url == "/console/users/"


def test_anonymous_is_sent_to_the_console_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next=/console/users/"


def test_active_staff_who_arent_superusers_are_refused(client, active_staff, url):
    assert client.get(url).status_code == 403


def test_users_render_by_username_with_name_email_sign_in_and_state(client, superuser, url):
    User = get_user_model()
    amy = User.objects.create_user(
        "amy",
        password="x",
        first_name="Amy",
        last_name="Pond",
        email="amy@example.com",
        is_superuser=True,
        last_login=timezone.now() - timedelta(hours=3),
    )
    User.objects.create_user("old", password="x", is_superuser=True, is_active=False)
    User.objects.create_user("bob", password="x", is_staff=True)

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/users.html"
    assert "<title>Users · Console</title>" in body
    assert body.index(">amy<") < body.index(">bob<") < body.index(">old<") < body.index(">root<")
    assert f'<li id="user-{amy.pk}"' in body
    assert "Amy Pond · amy@example.com · signed in <time" in body
    assert "3\xa0hours ago</time>" in body
    assert "Never signed in" in body
    assert '<span class="c-label c-tone-ok">Active</span>' in body
    assert '<span class="c-label c-tone-muted">Inactive</span>' in body
    assert '<span class="c-label c-tone-warning">Not a superuser</span>' in body
    assert body.count("Make superuser") == 1
    assert "Add a user" in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_users_sit_under_system_after_api_keys(client, superuser, url):
    body = client.get(url).content.decode()

    assert 'href="/console/users/" aria-current="page">Users</a>' in body
    assert body.index(">API keys</a>") < body.index(">Users</a>")
    assert '<a class="c-unav__item" href="/console/routes/" aria-current="page">' in body


def test_htmx_gets_the_list_without_the_layout(client, superuser, url):
    resp = client.get(url, HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_users.html"
    assert "<html" not in resp.content.decode()


def test_names_link_their_page_and_add_opens_the_new_user_page(client, superuser, url):
    body = client.get(url).content.decode()

    assert (
        f'<a id="user-{superuser.pk}-view" href="/console/users/{superuser.pk}/">root</a>' in body
    )
    assert (
        '<a id="users-add" class="c-button c-button--sm c-button--primary" '
        'href="/console/users/new/">Add a user</a>'
    ) in body


def test_make_superuser_reads_as_text_until_its_action_exists(client, superuser, url):
    get_user_model().objects.create_user("bob", password="x")

    body = client.get(url).content.decode()

    assert '<p class="c-description__action">Make superuser</p>' in body


def test_a_name_without_a_link_is_plain_text(client, superuser, url):
    with mock.patch("apps.console.intents.INTENTS", {}):
        body = client.get(url).content.decode()

    assert "<strong>root</strong>" in body
