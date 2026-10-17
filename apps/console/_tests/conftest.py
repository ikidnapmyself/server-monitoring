import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from apps.alerts._tests.fixtures import make_reevaluable_alert
from apps.alerts.models import Alert


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


@pytest.fixture
def superuser():
    return get_user_model().objects.create_superuser("root", password="x")


@pytest.fixture
def view_alert_user():
    user = get_user_model().objects.create_user("viewer", password="x", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="alerts", codename="view_alert")
    )
    return user


@pytest.fixture
def reevaluable_alert(db) -> Alert:
    return make_reevaluable_alert()
