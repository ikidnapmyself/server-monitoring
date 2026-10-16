import re
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.alerts.check_integration import CheckAlertResult
from apps.alerts.models import Alert, Incident
from apps.console._tests.views.test_incident_action import _notices
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["plain", "htmx"])
ENQUEUE = "apps.orchestration.intake.enqueue_for"
RUN_CHECKS = "apps.alerts.check_integration.CheckAlertBridge.run_checks_and_alert"
RAN = "Checks ran again. The incident updates when the run finishes."
UNCHANGED = "Checks ran again. Nothing changed."
REFUSED = "This incident&#x27;s checks can&#x27;t run on this machine."

pytestmark = pytest.mark.usefixtures("this_box")


@pytest.fixture
def this_box():
    with override_settings(INSTANCE_ID="this-box"):
        yield


@pytest.fixture
def run_checks():
    with mock.patch(RUN_CHECKS, return_value=CheckAlertResult()) as run:
        yield run


def _operator(name="op", *, can_rerun=True):
    user = get_user_model().objects.create_user(name, password="x", is_staff=True)
    if can_rerun:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="checkers", codename="add_checkrun")
        )
    return user


@pytest.fixture
def operator(client):
    user = _operator()
    client.force_login(user)
    return user


def _incident(instance="this-box", checker="cpu"):
    incident = Incident.objects.create(severity="critical", instance=instance)
    Alert.objects.create(
        fingerprint=f"check:{instance}:{checker}",
        source="cluster",
        name="CPU usage",
        labels={"checker": checker, "instance_id": instance},
        incident=incident,
        started_at=timezone.now(),
    )
    return incident


def _url(incident):
    return reverse("console:incident-rerun", args=[incident.pk])


def _page(incident):
    return reverse("console:incident", args=[incident.pk])


def test_the_rerun_lives_under_the_incident():
    assert reverse("console:incident-rerun", args=[7]) == "/console/incidents/7/rerun/"


@pytest.mark.django_db
def test_anonymous_post_is_sent_to_the_admin_login(client, run_checks):
    incident = _incident()

    resp = client.post(_url(incident))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_page(incident)}"
    run_checks.assert_not_called()


@pytest.mark.django_db
@BOTH
def test_staff_without_the_permission_is_refused(client, run_checks, headers):
    client.force_login(_operator(can_rerun=False))

    resp = client.post(_url(_incident()), **headers)

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to run checks required."
    run_checks.assert_not_called()


@pytest.mark.django_db
def test_get_is_not_allowed(client, operator, run_checks):
    resp = client.get(_url(_incident()))

    assert resp.status_code == 405
    assert resp["Allow"] == "POST"
    run_checks.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_incident_is_the_console_404_page(client, operator, run_checks, pk, headers):
    resp = client.post(f"/console/incidents/{pk}/rerun/", **headers)

    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "console/not_found.html"
    run_checks.assert_not_called()


@pytest.mark.django_db
def test_htmx_rerun_that_changed_nothing_says_so(client, operator, run_checks):
    incident = _incident()

    resp = client.post(_url(incident), **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/partials/_incident.html"
    assert _notices(body) == [("ok", UNCHANGED)]
    run_checks.assert_called_once_with(checker_names=["cpu"])
    assert list(resp.wsgi_request._messages) == []
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_htmx_rerun_that_enqueued_a_run_says_it_will_update(client, operator, run_checks):
    incident = _incident()

    with mock.patch(ENQUEUE, return_value=[mock.sentinel.run]):
        resp = client.post(_url(incident), **HTMX)

    assert resp.status_code == 200
    assert _notices(resp.content.decode()) == [("ok", RAN)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "incident_fields", [{"instance": "web-03"}, {"checker": "zfs_pool"}], ids=["remote", "unknown"]
)
def test_htmx_refusal_returns_a_warning_notice(client, operator, run_checks, incident_fields):
    resp = client.post(_url(_incident(**incident_fields)), **HTMX)

    assert resp.status_code == 200
    assert _notices(resp.content.decode()) == [("warning", REFUSED)]
    run_checks.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data, suffix", [({}, ""), ({"all": "1"}, "?all=1")], ids=["default", "all"]
)
def test_plain_post_redirects_with_a_message(client, operator, run_checks, data, suffix):
    incident = _incident()

    resp = client.post(_url(incident), data)

    assert resp.status_code == 303
    assert resp["Location"] == f"{_page(incident)}{suffix}"
    assert _notices(client.get(_page(incident)).content.decode()) == [("ok", UNCHANGED)]


@pytest.mark.django_db
def test_plain_refusal_redirects_with_a_warning(client, operator, run_checks):
    incident = _incident(instance="web-03")

    resp = client.post(_url(incident))

    assert resp.status_code == 303
    assert _notices(client.get(_page(incident)).content.decode()) == [("warning", REFUSED)]


@pytest.mark.django_db
@BOTH
def test_a_local_incident_with_a_known_checker_offers_the_rerun(client, operator, headers):
    incident = _incident()

    body = client.get(_page(incident), **headers).content.decode()

    button = re.search(r'<button id="action-rerun_check"[^>]*>Run the checks again</button>', body)
    assert button
    form = body[: body.index('id="action-rerun_check"')]
    form = form[form.rindex("<form") :]
    assert f'hx-post="{_url(incident)}"' in form
    assert 'hx-target="#incident"' in form
    assert form.count('name="csrfmiddlewaretoken"') == 1


@pytest.mark.django_db
@BOTH
def test_all_rides_along_on_the_rerun_form(client, operator, headers):
    incident = _incident()

    body = client.get(f"{_page(incident)}?all=1", **headers).content.decode()

    form = body[body.index(f'hx-post="{_url(incident)}"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="all" value="1">' in form


@pytest.mark.django_db
@pytest.mark.parametrize(
    "incident_fields", [{"instance": "web-03"}, {"checker": "zfs_pool"}], ids=["remote", "unknown"]
)
def test_an_incident_this_machine_cant_check_offers_no_rerun(client, operator, incident_fields):
    body = client.get(_page(_incident(**incident_fields))).content.decode()

    assert "action-rerun_check" not in body
    assert "Run the checks again" not in body


@pytest.mark.django_db
def test_staff_without_the_permission_sees_no_rerun(client):
    client.force_login(_operator(can_rerun=False))

    body = client.get(_page(_incident())).content.decode()

    assert "action-rerun_check" not in body
