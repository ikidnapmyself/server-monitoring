import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.queries.test_runs import _run
from apps.console.views import inbox as inbox_views
from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["plain", "htmx"])
PAGE = "/console/inbox/"
POSTS = ("/console/inbox/drain-oldest/", "/console/inbox/reclaim/", "/console/inbox/7/drain/")


@pytest.fixture(autouse=True)
def execute_run():
    """A drain in a view test claims for real and executes nothing."""
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        yield mock_exec


def _status(run):
    return PipelineRun.objects.get(pk=run.pk).status


def _stuck(run):
    PipelineRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(minutes=DEFAULT_STALE_MINUTES + 1)
    )
    return run


def _post(client, url, headers, data=None):
    resp = client.post(url, data or {}, **headers)
    if headers:
        assert resp.status_code == 200
        return resp
    assert resp.status_code == 303
    return client.get(resp["Location"])


def _flash(resp):
    return re.findall(
        r'class="c-flash c-tone-(\w+)"[^>]*><svg\b.*?</svg><span>(.*?)</span>',
        resp.content.decode(),
    )


def test_the_routes():
    assert reverse("console:inbox") == PAGE
    assert reverse("console:inbox-drain-oldest") == POSTS[0]
    assert reverse("console:inbox-reclaim") == POSTS[1]
    assert reverse("console:inbox-drain", args=[7]) == POSTS[2]


def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(PAGE)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={PAGE}"


def test_an_anonymous_post_lands_on_the_inbox_after_login(client):
    resp = client.post(POSTS[0])

    assert resp["Location"] == f"{reverse('admin:login')}?next={PAGE}"


def test_a_superuser_reads_the_page(client, superuser):
    _run(status="pending")

    resp = client.get(PAGE)

    assert resp.status_code == 200
    assert 'aria-current="page"' in resp.content.decode().split("Inbox</span>")[0][-300:]


@BOTH
def test_drain_now_runs_exactly_one_run(client, superuser, execute_run, headers):
    posted = _run(status="pending")
    other = _run(status="pending")

    resp = _post(client, reverse("console:inbox-drain", args=[posted.pk]), headers)

    assert execute_run.call_count == 1
    assert (_status(posted), _status(other)) == ("processing", "pending")
    assert ("ok", "Ran 1 run.") in _flash(resp)


@BOTH
def test_drain_now_on_a_run_already_claimed_warns(client, superuser, execute_run, headers):
    run = _run(status="processing")

    resp = _post(client, reverse("console:inbox-drain", args=[run.pk]), headers)

    assert execute_run.call_count == 0
    assert ("warning", "This run is no longer queued.") in _flash(resp)


def test_drain_now_on_a_missing_run_warns(client, superuser):
    resp = _post(client, reverse("console:inbox-drain", args=[404]), HTMX)

    assert ("warning", "This run is no longer queued.") in _flash(resp)


@BOTH
def test_drain_now_that_raises_reports_it(client, superuser, execute_run, headers):
    execute_run.side_effect = RuntimeError("boom")
    run = _run(status="pending")

    resp = _post(client, reverse("console:inbox-drain", args=[run.pk]), headers)

    assert ("critical", "1 run raised an error. Its page shows where it stopped.") in _flash(resp)


def test_some_ran_and_some_raised(client, superuser, execute_run):
    execute_run.side_effect = [None, RuntimeError("boom"), RuntimeError("boom")]
    for _ in range(3):
        _run(status="pending")

    resp = _post(client, POSTS[0], HTMX)

    assert ("critical", "Ran 1 run. 2 runs raised an error.") in _flash(resp)


def test_two_that_raise_and_none_that_ran(client, superuser, execute_run):
    execute_run.side_effect = RuntimeError("boom")
    _run(status="pending")
    _run(status="pending")

    resp = _post(client, POSTS[0], HTMX)

    assert ("critical", "2 runs raised an error. Their pages show where they stopped.") in _flash(
        resp
    )


def test_drain_the_oldest_drains_at_most_the_limit(client, superuser, execute_run, monkeypatch):
    monkeypatch.setattr(inbox_views, "DRAIN_LIMIT", 2)
    now = timezone.now()
    runs = [_run(status="pending") for _ in range(3)]
    for age, run in zip((3, 2, 1), runs, strict=True):
        PipelineRun.objects.filter(pk=run.pk).update(created_at=now - timedelta(minutes=age))

    resp = _post(client, POSTS[0], {})

    assert execute_run.call_count == 2
    assert [_status(run) for run in runs] == ["processing", "processing", "pending"]
    assert ("ok", "Ran 2 runs. 1 run left for the next drain.") in _flash(resp)


def test_drain_the_oldest_is_capped_below_the_worker_timeout():
    assert inbox_views.DRAIN_LIMIT <= 10
    assert inbox_views.DRAIN_SECONDS <= 60


def test_drain_the_oldest_stops_starting_runs_when_its_time_is_spent(
    client, superuser, execute_run
):
    runs = [_run(status="pending") for _ in range(3)]
    clock = iter([0.0, 0.0, 61.0])

    with patch("apps.orchestration.inbox.time.monotonic", side_effect=lambda: next(clock)):
        resp = _post(client, POSTS[0], HTMX)

    assert execute_run.call_count == 1
    assert sorted(_status(run) for run in runs) == ["pending", "pending", "processing"]
    assert ("ok", "Ran 1 run. 2 runs left for the next drain.") in _flash(resp)


def test_a_drain_that_raised_still_counts_what_is_left(client, superuser, execute_run, monkeypatch):
    monkeypatch.setattr(inbox_views, "DRAIN_LIMIT", 1)
    execute_run.side_effect = RuntimeError("boom")
    _run(status="pending")
    _run(status="pending")

    resp = _post(client, POSTS[0], HTMX)

    assert (
        "critical",
        "1 run raised an error. Its page shows where it stopped. 1 run left for the next drain.",
    ) in _flash(resp)


@BOTH
def test_drain_the_oldest_on_an_empty_queue_says_nothing_waited(
    client, superuser, execute_run, headers
):
    resp = _post(client, POSTS[0], headers)

    assert execute_run.call_count == 0
    assert ("info", "Nothing was waiting.") in _flash(resp)


@BOTH
def test_reclaim_returns_only_stuck_runs(client, superuser, headers):
    stuck = _stuck(_run(status="processing"))
    fresh = _run(status="processing")

    resp = _post(client, POSTS[1], headers)

    assert (_status(stuck), _status(fresh)) == ("pending", "processing")
    assert ("ok", "Returned 1 stuck run to the queue.") in _flash(resp)


def test_reclaim_counts_in_the_plural(client, superuser):
    _stuck(_run(status="processing"))
    _stuck(_run(status="processing"))

    resp = _post(client, POSTS[1], HTMX)

    assert ("ok", "Returned 2 stuck runs to the queue.") in _flash(resp)


def test_reclaim_with_nothing_stuck_says_so(client, superuser):
    resp = _post(client, POSTS[1], {})

    assert ("info", "No run was stuck.") in _flash(resp)


def test_a_plain_post_redirects_to_the_inbox_with_its_filter(client, superuser):
    resp = client.post(POSTS[1], {"status": "processing"})

    assert resp.status_code == 303
    assert resp["Location"] == "/console/inbox/?status=processing"


def test_a_plain_post_without_a_filter_redirects_to_the_bare_inbox(client, superuser):
    assert client.post(POSTS[1])["Location"] == PAGE


def test_an_htmx_post_keeps_the_filter(client, superuser):
    pending = _run(status="pending")
    busy = _run(status="processing")

    body = client.post(POSTS[1], {"status": "pending"}, **HTMX).content.decode()

    assert f'id="inbox-run-{pending.pk}"' in body
    assert f'id="inbox-run-{busy.pk}"' not in body


def test_the_partial_is_the_polled_root_and_actions_swap_it_whole(client, superuser):
    run = _run(status="pending")
    _stuck(_run(status="processing"))

    body = client.get(PAGE + "?status=pending", **HTMX).content.decode()

    assert body.startswith('<div id="inbox"')
    forms = re.findall(r"<form\b[^>]*hx-post=.*?</form>", body, re.S)
    assert len(forms) == 3
    for form in forms:
        assert 'hx-swap="outerHTML"' in form
        assert 'hx-target="#inbox"' in form
        assert '<input type="hidden" name="status" value="pending">' in form
    assert f'id="inbox-run-{run.pk}-drain"' in body
    assert 'id="action-drain_inbox"' in body
    assert 'id="action-reclaim_inbox"' in body


def test_without_a_filter_the_forms_carry_no_status(client, superuser):
    _run(status="pending")

    body = client.get(PAGE, **HTMX).content.decode()

    assert 'name="status"' not in body


def test_the_page_shows_stuck_runs_and_the_hidden_count(client, superuser, monkeypatch):
    from apps.console.queries import inbox as inbox_queries

    monkeypatch.setattr(inbox_queries, "INBOX_LIMIT", 1)
    stuck = _stuck(_run(status="processing"))
    PipelineRun.objects.filter(pk=stuck.pk).update(created_at=timezone.now() - timedelta(hours=1))
    _run(status="pending")

    body = client.get(PAGE).content.decode()

    assert f'id="inbox-run-{stuck.pk}"' in body
    assert "c-label c-tone-critical" in body
    assert "1 more not shown" in body
    assert f'href="/console/runs/{stuck.pk}/"' in body


def test_an_empty_queue_says_nothing_is_waiting(client, superuser):
    assert "Nothing is waiting." in client.get(PAGE).content.decode()


def test_a_filter_that_hides_the_queue_offers_to_clear_it(client, superuser):
    _run(status="pending")

    body = client.get(PAGE + "?status=processing").content.decode()

    assert "Nothing is in progress." in body
    assert 'id="filters-clear"' in body


def test_an_invalid_filter_is_flagged(client, superuser):
    body = client.get(PAGE + "?status=nope").content.decode()

    assert "Ignored invalid filter: status." in body


def test_the_incident_link_is_on_the_row(client, superuser):
    from apps.console._tests.queries.test_runs import _incident

    incident = _incident()
    run = _run(status="pending", incident=incident)

    body = client.get(PAGE).content.decode()

    assert f'id="inbox-run-{run.pk}-incident" href="/console/incidents/{incident.pk}/"' in body
