import pytest
from django.contrib.auth import get_user_model

from apps.alerts._tests.fixtures import make_reevaluable_alert
from apps.alerts.models import Alert


@pytest.fixture
def superuser(client):
    user = get_user_model().objects.create_superuser("root", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def active_staff(client):
    """Active staff who aren't superusers: the console refuses them."""
    user = get_user_model().objects.create_user("staff", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.fixture
def reevaluable_alert(db) -> Alert:
    return make_reevaluable_alert()
