import re
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.alerts.models import Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import named_incident
from apps.console.views.incident import IncidentPageView
from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus, StageExecution

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["plain", "htmx"])
DONE = {"acknowledge": "acknowledged", "resolve": "resolved", "close": "closed"}
LABELS = {"acknowledge": "Acknowledge", "resolve": "Resolve", "close": "Close"}
AVAILABLE = {
    "open": ["acknowledge", "resolve", "close"],
    "acknowledged": ["resolve", "close"],
    "resolved": ["close"],
    "closed": [],
}


def _operator(name="op", *, can_act=True):
    user = get_user_model().objects.create_user(name, password="x", is_staff=True)
    if can_act:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="alerts", codename="change_incident")
        )
    return user


@pytest.fixture
def operator(client):
    user = _operator()
    client.force_login(user)
    return user


def _incident(status="open", **fields):
    return Incident.objects.create(severity="critical", status=status, **fields)


def _url(incident, action):
    return reverse("console:incident-action", args=[incident.pk, action])


def _page(incident):
    return reverse("console:incident", args=[incident.pk])


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _forms(body):
    return re.findall(r"<form\b[^>]*hx-post=.*?</form>", body, re.S)


def _notices(body):
    return re.findall(
        r'<div\b[^>]*\sclass="c-flash c-tone-(\w+)"[^>]*><svg\b.*?</svg><span>(.*?)</span></div>',
        body,
    )


def test_the_action_lives_under_the_incident():
    assert (
        reverse("console:incident-action", args=[7, "resolve"]) == "/console/incidents/7/resolve/"
    )


@pytest.mark.django_db
def test_anonymous_post_is_sent_to_the_admin_login(client):
    incident = _incident()

    resp = client.post(_url(incident, "resolve"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_page(incident)}"
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_logging_in_after_an_anonymous_post_lands_on_the_incident_page(client):
    user = _operator()
    user.set_password("pw")
    user.save()
    incident = _incident()

    refused = client.post(_url(incident, "resolve"))
    landed = client.post(refused["Location"], {"username": "op", "password": "pw"}, follow=True)

    assert landed.redirect_chain == [(_page(incident), 302)]
    assert landed.status_code == 200
    assert landed.templates[0].name == "console/incident.html"
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_anonymous_htmx_post_without_a_usable_page_falls_back_to_the_incident(client):
    incident = _incident()

    resp = client.post(
        _url(incident, "resolve"), HTTP_HX_CURRENT_URL="http://evil.example/x", **HTMX
    )

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next={_page(incident)}"


@pytest.mark.django_db
def test_anonymous_htmx_post_is_redirected_back_to_the_page_it_came_from(client):
    incident = _incident()

    resp = client.post(
        _url(incident, "resolve"),
        HTTP_HX_CURRENT_URL=f"http://testserver{_page(incident)}",
        **HTMX,
    )

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next={_page(incident)}"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_non_staff_is_refused_even_with_the_permission(client):
    user = _operator("visitor")
    user.is_staff = False
    user.save()
    client.force_login(user)
    incident = _incident()

    resp = client.post(_url(incident, "resolve"))

    assert resp.status_code == 403
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
@BOTH
def test_staff_without_the_change_permission_is_refused(client, headers):
    client.force_login(_operator(can_act=False))
    incident = _incident()

    resp = client.post(_url(incident, "resolve"), **headers)

    assert resp.status_code == 403
    assert "script-src 'self'" in resp["Content-Security-Policy"]
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_the_permission_is_checked_before_the_record_or_the_action(client):
    client.force_login(_operator(can_act=False))

    assert client.post("/console/incidents/404/resolve/").status_code == 403
    assert client.post(_url(_incident(), "reopen")).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("action", sorted(DONE))
def test_get_is_not_allowed(client, operator, action):
    incident = _incident()

    resp = client.get(_url(incident, action))

    assert resp.status_code == 405
    assert resp["Allow"] == "POST"
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["reopen", "delete", "Resolve", "acknowledge_incident"])
@BOTH
def test_an_unknown_action_is_a_404(client, operator, action, headers):
    incident = _incident()

    resp = client.post(_url(incident, action), **headers)

    assert resp.status_code == 404
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_incident_is_the_console_404_page(client, operator, pk, headers):
    resp = client.post(f"/console/incidents/{pk}/resolve/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "console/not_found.html"
    assert 'id="not-found-back"' in body
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("action", sorted(DONE))
@BOTH
def test_each_action_transitions_and_enqueues_one_manual_run(client, operator, action, headers):
    incident = _incident()

    resp = client.post(_url(incident, action), **headers)

    assert resp.status_code == (200 if headers else 303)
    incident.refresh_from_db()
    assert incident.status == DONE[action]
    run = PipelineRun.objects.get()
    assert run.incident_id == incident.pk
    assert run.origin == PipelineOrigin.MANUAL
    assert run.status == PipelineStatus.PENDING
    assert not StageExecution.objects.exists()


@pytest.mark.django_db
def test_the_operator_is_recorded_as_the_actor(client, operator):
    acknowledged, resolved = _incident(), _incident()

    client.post(_url(acknowledged, "acknowledge"))
    client.post(_url(resolved, "resolve"))

    assert Incident.objects.get(pk=acknowledged.pk).metadata == {"acknowledged_by": "op"}
    assert Incident.objects.get(pk=resolved.pk).metadata == {"resolved_by": "op"}


@pytest.mark.django_db
def test_an_htmx_post_gets_the_partial_with_a_success_message(client, operator):
    incident = _incident()

    resp = client.post(_url(incident, "acknowledge"), **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/partials/_incident.html"
    assert "<html" not in body and 'id="incident"' not in body
    assert _notices(body) == [("ok", f"Incident #{incident.pk} acknowledged.")]
    notice = _tag(body, "incident-notice")
    assert 'role="status"' in notice and 'tabindex="-1"' in notice
    assert re.search(r"\sdata-fresh[\s>]", notice)
    assert body.lstrip().startswith(notice)
    assert '<span class="c-state c-status--acknowledged">' in body
    assert 'id="action-acknowledge"' not in body
    assert 'id="action-resolve"' in body
    assert "script-src 'self'" in resp["Content-Security-Policy"]
    assert "no-store" in resp["Cache-Control"]


@pytest.mark.django_db
def test_a_plain_post_redirects_to_the_page_which_shows_the_message_once(client, operator):
    incident = _incident()

    resp = client.post(_url(incident, "resolve"))

    assert resp.status_code == 303
    assert resp["Location"] == _page(incident)
    assert _notices(client.get(_page(incident), **HTMX).content.decode()) == []
    body = client.get(_page(incident)).content.decode()
    assert _notices(body) == [("ok", f"Incident #{incident.pk} resolved.")]
    assert body.index('<div class="c-flash') < body.index('<div id="incident"')
    assert "data-fresh" not in body and 'id="incident-notice"' not in body
    assert _notices(client.get(_page(incident)).content.decode()) == []


@pytest.mark.django_db
def test_an_htmx_outcome_never_reaches_the_messages_framework(client, operator):
    incident = _incident()

    acted = client.post(_url(incident, "acknowledge"), **HTMX)
    refused = client.post(_url(incident, "acknowledge"), **HTMX)

    assert list(acted.wsgi_request._messages) == []
    assert list(refused.wsgi_request._messages) == []
    assert _notices(client.get(_page(incident), **HTMX).content.decode()) == []
    assert _notices(client.get(_page(incident)).content.decode()) == []


@pytest.mark.django_db
def test_a_poll_leaves_another_surfaces_message_alone(client, operator):
    incident = _incident("closed")
    client.post(_url(incident, "close"))

    for _ in range(2):
        assert _notices(client.get(_page(incident), **HTMX).content.decode()) == []

    assert _notices(client.get(_page(incident)).content.decode()) == [
        ("warning", "Already closed.")
    ]


@pytest.mark.django_db
def test_a_lookup_failure_inside_the_announce_is_not_a_missing_incident(client, operator):
    incident = _incident()

    with mock.patch(
        "apps.alerts.services.announce_incident_change", side_effect=Node.DoesNotExist("gone")
    ):
        with pytest.raises(Node.DoesNotExist):
            client.post(_url(incident, "resolve"), **HTMX)

    assert Incident.objects.get(pk=incident.pk).status == "open"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "action", "text"),
    [
        ("acknowledged", "acknowledge", "Cannot acknowledge — status is &#x27;acknowledged&#x27;."),
        ("resolved", "resolve", "Already resolved."),
        ("closed", "resolve", "Already closed."),
        ("closed", "close", "Already closed."),
    ],
)
def test_a_stale_action_re_renders_with_a_warning_and_no_run(
    client, operator, status, action, text
):
    incident = _incident(status)

    resp = client.post(_url(incident, action), **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/partials/_incident.html"
    assert _notices(body) == [("warning", text)]
    assert Incident.objects.get(pk=incident.pk).status == status
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_a_stale_plain_post_still_redirects_and_warns(client, operator):
    incident = _incident("closed")

    resp = client.post(_url(incident, "close"), follow=True)

    assert resp.redirect_chain == [(_page(incident), 303)]
    assert _notices(resp.content.decode()) == [("warning", "Already closed.")]
    assert not PipelineRun.objects.exists()


@pytest.mark.django_db
def test_a_double_submit_announces_once(client, operator):
    incident = _incident()

    first = client.post(_url(incident, "acknowledge"), **HTMX)
    second = client.post(_url(incident, "acknowledge"), **HTMX)

    assert [tone for tone, _ in _notices(first.content.decode())] == ["ok"]
    assert [tone for tone, _ in _notices(second.content.decode())] == ["warning"]
    assert PipelineRun.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(("posted", "kept"), [("1", True), ("0", False), ("", False)])
def test_all_survives_only_when_the_form_posted_it(client, operator, monkeypatch, posted, kept):
    monkeypatch.setattr("apps.console.queries.incident.TIMELINE_LIMIT", 0)
    incident = _incident()
    PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    data = {"all": posted} if posted else {}

    plain = client.post(_url(incident, "acknowledge"), data)
    partial = client.post(_url(incident, "resolve"), data, **HTMX).content.decode()

    assert plain["Location"] == _page(incident) + ("?all=1" if kept else "")
    assert ('id="timeline-all"' not in partial) is kept
    for form in _forms(partial):
        assert ('<input type="hidden" name="all" value="1">' in form) is kept


@pytest.mark.django_db
def test_all_in_the_querystring_of_the_action_url_is_ignored(client, operator):
    incident = _incident()

    resp = client.post(_url(incident, "acknowledge") + "?all=1")

    assert resp["Location"] == _page(incident)


@pytest.mark.django_db
def test_a_resolve_note_is_stripped_capped_and_stored(client, operator, monkeypatch):
    monkeypatch.setattr(IncidentPageView, "summary_limit", 5)
    incident = _incident()

    client.post(_url(incident, "resolve"), {"summary": "  rotated the logs  "})

    assert Incident.objects.get(pk=incident.pk).summary == "rotat"


@pytest.mark.django_db
@pytest.mark.parametrize("limit", [2000, 7])
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_the_field_carries_the_cap_the_view_enforces(client, operator, monkeypatch, limit, headers):
    assert IncidentPageView.summary_limit == 2000
    monkeypatch.setattr(IncidentPageView, "summary_limit", limit)
    incident = _incident()

    body = client.get(_page(incident), **headers).content.decode()

    assert f'maxlength="{limit}"' in _tag(body, "resolve-summary")


@pytest.mark.django_db
def test_a_note_at_the_cap_keeps_its_tail_when_the_browser_sends_crlf(client, operator):
    lines = ["123456789"] * 200
    assert len("\n".join(lines)) == 1999
    incident = _incident()

    client.post(_url(incident, "resolve"), {"summary": "\r\n".join(lines)})

    assert Incident.objects.get(pk=incident.pk).summary == "\n".join(lines)


@pytest.mark.django_db
def test_a_nul_in_the_note_is_dropped(client, operator):
    incident = _incident()

    client.post(_url(incident, "resolve"), {"summary": "ro\x00tated\x00"})

    assert Incident.objects.get(pk=incident.pk).summary == "rotated"


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["acknowledge", "close"])
def test_a_note_is_read_only_for_resolve(client, operator, action):
    incident = _incident(summary="earlier")

    client.post(_url(incident, action), {"summary": "ignored"})

    assert Incident.objects.get(pk=incident.pk).summary == "earlier"


@pytest.mark.django_db
def test_a_blank_note_keeps_the_existing_summary(client, operator):
    incident = _incident(summary="earlier")

    client.post(_url(incident, "resolve"), {"summary": "   "})

    assert Incident.objects.get(pk=incident.pk).summary == "earlier"


@pytest.mark.django_db
def test_a_hostile_note_and_title_are_stored_verbatim_and_rendered_escaped(client, operator):
    incident = named_incident(f"Disk {MARKUP}", severity="critical", status="open")

    body = client.post(_url(incident, "resolve"), {"summary": MARKUP}, **HTMX).content.decode()

    assert Incident.objects.get(pk=incident.pk).summary == MARKUP
    assert MARKUP not in body
    assert 'id="incident-notice"' in body
    assert f'<p class="c-prose">{ESCAPED}</p>' in body
    assert f"Disk {ESCAPED}" in body
    assert _notices(body) == [("ok", f"Incident #{incident.pk} resolved.")]


@pytest.mark.django_db
def test_csrf_is_enforced_and_a_polled_token_stays_valid(operator):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    incident = _incident()

    refused = client.post(_url(incident, "acknowledge"), **HTMX)

    assert refused.status_code == 403
    assert "script-src 'self'" in refused["Content-Security-Policy"]
    assert Incident.objects.get(pk=incident.pk).status == "open"
    assert not PipelineRun.objects.exists()

    polls = [client.get(_page(incident), **HTMX) for _ in range(2)]
    tokens = [
        re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', poll.content.decode()).group(1)
        for poll in polls
    ]
    assert tokens[0] != tokens[1]
    for poll in polls:
        assert "Cookie" in poll["Vary"] and "HX-Request" in poll["Vary"]
        assert "no-store" in poll["Cache-Control"]

    stale_render = client.post(
        _url(incident, "acknowledge"), {"csrfmiddlewaretoken": tokens[0]}, **HTMX
    )
    by_header = client.post(_url(incident, "resolve"), HTTP_X_CSRFTOKEN=tokens[1], **HTMX)

    assert stale_render.status_code == 200
    assert by_header.status_code == 200
    assert Incident.objects.get(pk=incident.pk).status == "resolved"
    assert PipelineRun.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("status", sorted(AVAILABLE))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_buttons_follow_the_status(client, operator, status, headers):
    incident = _incident(status)

    body = client.get(_page(incident), **headers).content.decode()

    assert re.findall(r'<button id="action-(\w+)"', body) == AVAILABLE[status]
    if "resolve" in AVAILABLE[status]:
        assert body.index('id="resolve-note"') > body.index('id="action-close"')
    assert ('id="resolve-note"' in body) is ("resolve" in AVAILABLE[status])
    assert ('class="c-actions"' in body) is bool(AVAILABLE[status])
    for action in AVAILABLE[status]:
        button = _tag(body, f"action-{action}")
        assert 'type="submit"' in button
        assert f">{LABELS[action]}</button>" in body[body.index(button) :]


@pytest.mark.django_db
def test_every_form_posts_to_its_action_with_a_token_and_the_htmx_contract(client, operator):
    incident = _incident()

    body = client.get(_page(incident), **HTMX).content.decode()

    forms = _forms(body)
    assert len(forms) == 4
    targets = []
    for form in forms:
        opening = form[: form.index(">") + 1]
        url = re.search(r'hx-post="([^"]+)"', opening).group(1)
        targets.append(url)
        assert 'method="post"' in opening
        assert f'action="{url}"' in opening
        assert 'hx-target="#incident"' in opening
        assert 'hx-swap="innerHTML"' in opening
        assert 'hx-sync="#incident:replace"' in opening
        assert 'hx-disabled-elt="#incident .c-button"' in opening
        assert re.search(r'class="c-button[ "]', re.search(r"<button\b[^>]*>", form).group())
        assert form.count('name="csrfmiddlewaretoken"') == 1
        assert form.count("<button") == 1
        assert 'name="all"' not in form
    assert targets == [
        _url(incident, "acknowledge"),
        _url(incident, "resolve"),
        _url(incident, "close"),
        _url(incident, "resolve"),
    ]


@pytest.mark.django_db
def test_the_resolve_note_is_a_details_with_its_own_form(client, operator):
    incident = _incident()

    body = client.get(_page(incident), **HTMX).content.decode()

    details = body[body.index('<details id="resolve-note"') :]
    details = details[: details.index("</details>")]
    assert '<summary id="resolve-note-summary">' in details
    assert f'hx-post="{_url(incident, "resolve")}"' in details
    field = _tag(details, "resolve-summary")
    assert 'name="summary"' in field
    assert f'maxlength="{IncidentPageView.summary_limit}"' in field
    assert '<label for="resolve-summary">' in details
    assert 'id="action-resolve-note"' in details


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_staff_without_the_permission_sees_no_buttons_and_no_container(client, headers):
    client.force_login(_operator(can_act=False))
    incident = _incident()

    resp = client.get(_page(incident), **headers)

    body = resp.content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]
    assert resp.status_code == 200
    assert "c-actions" not in region
    assert "<form" not in region and "<button" not in region
    assert "csrfmiddlewaretoken" not in region
    assert "resolve-note" not in region


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_every_focusable_element_with_buttons_has_a_unique_id(client, operator, headers):
    incident = _incident()

    body = client.get(_page(incident), **headers).content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]

    tags = re.findall(r"<(?:a|summary|details|pre|button|input|select|textarea)\b[^>]*>", region)
    focusable = [tag for tag in tags if 'type="hidden"' not in tag]
    assert len(tags) - len(focusable) == 4
    assert len(focusable) == 2 + 4 + 3
    assert all(re.search(r'\sid="[^"]+"', tag) for tag in focusable)
    ids = re.findall(r'\sid="([^"]*)"', region)
    assert len(ids) == len(set(ids))


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_the_rendered_page_with_buttons_and_a_message_is_clean(client, operator, label):
    incident = _incident()

    page = client.get(_page(incident)).content.decode()
    acted = client.post(_url(incident, "acknowledge"), **HTMX).content.decode()
    client.post(_url(incident, "acknowledge"))
    warned = client.get(_page(incident)).content.decode()

    assert 'id="action-acknowledge"' in page and 'id="resolve-note"' in page
    assert [tone for tone, _ in _notices(acted)] == ["ok"] and 'id="action-resolve"' in acted
    assert [tone for tone, _ in _notices(warned)] == ["warning"] and "<html" in warned
    for body in (page, acted, warned):
        assert not FORBIDDEN[label].search(body), label
