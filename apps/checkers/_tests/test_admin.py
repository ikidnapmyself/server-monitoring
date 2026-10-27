import pytest
from django.contrib import admin
from django.urls import reverse

from apps.checkers.models import CheckRun, PreflightRun

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("model", "list_display", "changelist", "row"),
    [
        (
            CheckRun,
            ["checker_name", "hostname", "status", "executed_at"],
            "admin:checkers_checkrun_changelist",
            lambda: CheckRun.objects.create(checker_name="listed", status="ok", message="m"),
        ),
        (
            PreflightRun,
            ["created_at", "overall_status", "passed", "warnings", "errors"],
            "admin:checkers_preflightrun_changelist",
            lambda: PreflightRun.objects.create(overall_status="warn", warnings=1),
        ),
    ],
)
def test_the_admin_is_stock_and_editable(
    admin_client, rf, admin_user, model, list_display, changelist, row
):
    created = row()
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.list_display == list_display
    assert not model_admin.inlines
    response = admin_client.get(reverse(changelist))
    assert response.status_code == 200
    change = reverse(changelist.replace("changelist", "change"), args=[created.pk])
    assert admin_client.get(change).status_code == 200


def test_the_check_run_alert_is_a_raw_id():
    assert admin.site._registry[CheckRun].raw_id_fields == ["alert"]
