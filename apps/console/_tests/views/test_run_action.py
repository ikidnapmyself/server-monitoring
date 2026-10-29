import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.queries.test_runs import _run
from apps.console.views.run import ABANDONED, NOT_FAILED, NOT_STUCK, RETRIED
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["plain", "htmx"])


def _url(run, action):
    return reverse("console:run-action", args=[run.pk, action])


def _page(run):
    return reverse("console:run", args=[run.pk])


def _status(run):
    return PipelineRun.objects.get(pk=run.pk).status


def _notices(body):
    return re.findall(
        r'<div\b[^>]*\sclass="c-flash c-tone-(\w+)"[^>]*><svg\b.*?</svg><span>(.*?)</span></div>',
        body,
    )


def test_the_action_lives_under_the_run():
    assert reverse("console:run-action", args=[7, "retry"]) == "/console/runs/7/retry/"


@pytest.mark.django_db
def test_anonymous_post_is_sent_to_the_admin_login(client):
    run = _run(status="failed")

    resp = client.post(_url(run, "retry"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_page(run)}"
    assert _status(run) == "failed"


@pytest.mark.django_db
def test_anonymous_htmx_post_is_redirected_back_to_the_page_it_came_from(client):
    run = _run(status="failed")

    resp = client.post(
        _url(run, "retry"), HTTP_HX_CURRENT_URL=f"http://testserver{_page(run)}", **HTMX
    )

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next={_page(run)}"
    assert _status(run) == "failed"


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["retry", "abandon"])
def test_get_is_not_allowed(client, superuser, action):
    run = _run(status="failed")

    resp = client.get(_url(run, action))

    assert resp.status_code == 405
    assert resp["Allow"] == "POST"
    assert _status(run) == "failed"


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["requeue", "Retry", "drain"])
@BOTH
def test_an_unknown_action_is_a_404(client, superuser, action, headers):
    run = _run(status="failed")

    resp = client.post(_url(run, action), **headers)

    assert resp.status_code == 404
    assert _status(run) == "failed"


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@pytest.mark.parametrize("action", ["retry", "abandon"])
@BOTH
def test_a_missing_run_is_the_console_404_page(client, superuser, pk, action, headers):
    resp = client.post(f"/console/runs/{pk}/{action}/", **headers)

    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "console/not_found.html"
    assert 'id="not-found-back"' in resp.content.decode()


@pytest.mark.django_db
@BOTH
def test_retry_requeues_a_failed_run(client, superuser, headers):
    run = _run(status="failed", total_attempts=1)

    resp = client.post(_url(run, "retry"), **headers)

    run.refresh_from_db()
    assert run.status == "pending"
    assert run.total_attempts == 2
    if headers:
        body = resp.content.decode()
        assert resp.status_code == 200
        assert resp.templates[0].name == "console/partials/_run.html"
        assert _notices(body) == [("ok", RETRIED.replace("'", "&#x27;"))]
        assert body.lstrip().startswith('<div id="run-notice"')
        assert "Mark failed" in body and "action-retry_run" not in body
    else:
        assert resp.status_code == 303
        assert resp["Location"] == _page(run)


@pytest.mark.django_db
@BOTH
def test_retry_on_a_run_that_is_no_longer_failed_warns(client, superuser, headers):
    run = _run(status="pending", total_attempts=1)

    resp = client.post(_url(run, "retry"), follow=not headers, **headers)

    assert _notices(resp.content.decode()) == [("warning", NOT_FAILED)]
    run.refresh_from_db()
    assert (run.status, run.total_attempts) == ("pending", 1)


@pytest.mark.django_db
def test_a_double_retry_queues_once(client, superuser):
    run = _run(status="failed", total_attempts=1)

    first = client.post(_url(run, "retry"), **HTMX)
    second = client.post(_url(run, "retry"), **HTMX)

    assert [tone for tone, _ in _notices(first.content.decode())] == ["ok"]
    assert _notices(second.content.decode()) == [("warning", NOT_FAILED)]
    assert PipelineRun.objects.get(pk=run.pk).total_attempts == 2


@pytest.mark.django_db
@pytest.mark.parametrize(("status", "minutes"), [("pending", 0), ("checked", 60), ("retrying", 60)])
@BOTH
def test_mark_failed_fails_a_queued_or_stuck_run(client, superuser, status, minutes, headers):
    run = _run(status=status)
    PipelineRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(minutes=minutes)
    )

    resp = client.post(_url(run, "abandon"), follow=not headers, **headers)

    assert _notices(resp.content.decode()) == [("ok", ABANDONED)]
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.last_error_type == "ManualOverride"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "completed"), [("notified", True), ("processing", False), ("retrying", False)]
)
@BOTH
def test_mark_failed_on_a_live_or_finished_run_warns(client, superuser, status, completed, headers):
    run = _run(status=status, completed_at=timezone.now() if completed else None)

    resp = client.post(_url(run, "abandon"), follow=not headers, **headers)

    assert _notices(resp.content.decode()) == [("warning", NOT_STUCK)]
    assert _status(run) == status


@pytest.mark.django_db
def test_a_plain_post_shows_its_message_once(client, superuser):
    run = _run(status="failed")

    resp = client.post(_url(run, "retry"), follow=True)

    body = resp.content.decode()
    assert resp.redirect_chain == [(_page(run), 303)]
    assert _notices(body) == [("ok", RETRIED.replace("'", "&#x27;"))]
    assert body.index('<div class="c-flash') < body.index('<div id="run"')
    assert 'id="run-notice"' not in body
    assert _notices(client.get(_page(run)).content.decode()) == []


@pytest.mark.django_db
def test_an_htmx_outcome_never_reaches_the_messages_framework(client, superuser):
    run = _run(status="failed")

    acted = client.post(_url(run, "retry"), **HTMX)
    refused = client.post(_url(run, "retry"), **HTMX)

    assert list(acted.wsgi_request._messages) == []
    assert list(refused.wsgi_request._messages) == []
    assert _notices(client.get(_page(run), **HTMX).content.decode()) == []
    assert _notices(client.get(_page(run)).content.decode()) == []


@pytest.mark.django_db
@pytest.mark.parametrize(("status", "action"), [("failed", "retry"), ("pending", "abandon")])
def test_the_button_posts_with_the_htmx_contract(client, superuser, status, action):
    run = _run(status=status)

    body = client.get(_page(run)).content.decode()

    form = re.search(r"<form\b[^>]*hx-post=.*?</form>", body, re.S).group()
    assert f'action="{_url(run, action)}"' in form
    assert f'hx-post="{_url(run, action)}"' in form
    assert 'hx-target="#run"' in form and 'hx-swap="innerHTML"' in form
    assert 'hx-sync="#run:replace"' in form
    assert 'hx-disabled-elt="#run .c-button"' in form
    assert 'name="csrfmiddlewaretoken"' in form
