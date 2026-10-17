import pytest

from apps.alerts._tests.fixtures import make_reevaluable_alert
from apps.alerts.models import Alert


@pytest.fixture
def reevaluable_alert(db) -> Alert:
    return make_reevaluable_alert()
