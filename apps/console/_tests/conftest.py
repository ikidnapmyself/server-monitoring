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


def _perm(user, app_label, codename):
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label=app_label, codename=codename)
    )
    return user


@pytest.fixture
def run_viewer(bare_staff):
    return _perm(bare_staff, "orchestration", "view_pipelinerun")


@pytest.fixture
def run_operator(run_viewer):
    return _perm(run_viewer, "orchestration", "change_pipelinerun")


@pytest.fixture
def inbox_viewer(bare_staff):
    return _perm(bare_staff, "orchestration", "view_inboxitem")


@pytest.fixture
def inbox_operator(inbox_viewer):
    return _perm(inbox_viewer, "orchestration", "change_inboxitem")


@pytest.fixture
def node_viewer(bare_staff):
    return _perm(bare_staff, "alerts", "view_node")


@pytest.fixture
def node_operator(node_viewer):
    return _perm(node_viewer, "alerts", "change_node")
