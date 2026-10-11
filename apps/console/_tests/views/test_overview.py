import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


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
