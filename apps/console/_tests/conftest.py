import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission


@pytest.fixture
def bare_staff(client):
    """Active staff with no model permissions: the console refuses its reads."""
    user = get_user_model().objects.create_user("bare", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="alerts", codename="view_incident")
    )
    client.force_login(user)
    return user
