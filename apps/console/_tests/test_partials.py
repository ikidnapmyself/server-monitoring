"""The shared partials render a Description, its action and its Details."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import timezone

from apps.console.intents import ResolvedAction
from config.describing import Action, Description, Fact


@pytest.fixture
def user(db):
    return get_user_model().objects.create_superuser("root", password="x")


def _render(user, d, **context):
    request = RequestFactory().get("/console/")
    request.user = user
    return render_to_string(
        "console/partials/_description.html", {"d": d, "id": "disk", **context}, request=request
    )


def test_headline_tone_and_detail(user):
    html = _render(user, Description("Disk is filling", "warning", detail="92% used"))

    assert 'class="c-description c-tone-warning"' in html
    assert "Disk is filling" in html
    assert '<p class="c-description__detail">92% used</p>' in html
    assert "octicon-alert" in html


def test_no_detail_no_action_no_facts(user):
    html = _render(user, Description("All good", "ok"))

    assert "c-description__detail" not in html
    assert "c-description__action" not in html
    assert "<form" not in html
    assert "<a " not in html
    assert "data-details" not in html


def test_post_action_is_a_form(user):
    d = Description("Open", "critical", action=Action("Acknowledge", "acknowledge", {"pk": 7}))

    html = _render(user, d)

    assert '<form method="post" action="/console/incidents/7/acknowledge/"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert '<button id="disk-action" class="c-button c-button--sm" type="submit">' in html
    assert "hx-post" not in html
    assert 'name="all"' not in html


def test_post_action_with_htmx_target_and_all(user):
    d = Description("Open", "critical", action=Action("Acknowledge", "acknowledge", {"pk": 7}))

    html = _render(user, d, hx_target="#incident", all=True)

    assert 'hx-post="/console/incidents/7/acknowledge/"' in html
    assert 'hx-target="#incident"' in html
    assert 'hx-swap="innerHTML"' in html
    assert 'hx-sync="#incident:replace"' in html
    assert 'hx-disabled-elt="#incident .c-button"' in html
    assert '<input type="hidden" name="all" value="1">' in html


def test_post_action_with_htmx_target_without_all(user):
    d = Description("Open", "critical", action=Action("Acknowledge", "acknowledge", {"pk": 7}))

    html = _render(user, d, hx_target="#incident")

    assert 'hx-target="#incident"' in html
    assert 'name="all"' not in html


def test_get_action_is_a_link(user):
    d = Description("Node quiet", "warning", action=Action("Open nodes", "view_nodes"))

    html = _render(user, d)

    assert '<a id="disk-action" class="c-button c-button--sm" href="/console/nodes/"' in html
    assert "noopener" not in html
    assert "<form" not in html


def test_external_link_opens_apart(user):
    d = Description(
        "Read the runbook", "info", action=Action("Runbook", "open_url", {"url": "https://x.test/"})
    )

    html = _render(user, d)

    assert 'href="https://x.test/"' in html
    assert 'rel="noopener noreferrer" target="_blank"' in html


def test_an_unknown_intent_is_text(user):
    d = Description("Open", "critical", action=Action("Acknowledge it", "nope", {"pk": 7}))

    html = _render(user, d)

    assert '<p class="c-description__action">Acknowledge it</p>' in html
    assert "<form" not in html
    assert "<a " not in html


def test_facts_render_in_details_with_copy(user):
    d = Description(
        "Disk is filling",
        "warning",
        facts=(Fact("Mount", "/var"), Fact("Fingerprint", "check:a:disk", copy=True)),
    )

    html = _render(user, d)

    assert '<details id="disk-details" class="c-details" data-details>' in html
    assert '<summary id="disk-details-summary">Details</summary>' in html
    assert "<dt>Mount</dt><dd>/var</dd>" in html
    assert (
        '<button id="disk-details-copy-2" type="button" class="c-copy" data-copy="check:a:disk" '
        'aria-label="Copy Fingerprint">Copy</button>'
    ) in html
    assert html.count('class="c-copy"') == 1
    assert "c-entry__raw" not in html


def test_raw_json_sits_last_inside_details(user):
    d = Description("Disk", "warning", facts=(Fact("Mount", "/var"),))

    html = _render(user, d, raw='{"a": "<b>"}')

    assert '<details id="disk-details-raw" class="c-entry__raw">' in html
    assert '<summary id="disk-details-raw-summary">Raw JSON</summary>' in html
    assert "{&quot;a&quot;: &quot;&lt;b&gt;&quot;}" in html
    assert html.index("disk-details-raw") > html.index("</dl>")
    assert html.index("disk-details-raw") < html.rindex("</details>")


def test_time_renders_relative_with_exact_title():
    when = timezone.now() - timedelta(minutes=5)

    html = render_to_string("console/partials/_time.html", {"when": when})

    assert f'datetime="{when.isoformat()}"' in html
    assert 'title="' in html
    assert "5\xa0minutes ago</time>" in html


def test_headless_leaves_the_headline_to_its_container(user):
    html = _render(user, Description("Disk", "warning", detail="92% used"), headless=True)

    assert "c-description__headline" not in html
    assert "Disk" not in html
    assert '<p class="c-description__detail">92% used</p>' in html


def test_raw_without_facts_still_gets_details(user):
    html = _render(user, Description("Ran", "ok"), raw='{"a": 1}')

    assert '<details id="disk-details" class="c-details" data-details>' in html
    assert "<dl" not in html
    assert '<details id="disk-details-raw" class="c-entry__raw">' in html


def test_an_action_takes_an_extra_class(user):
    request = RequestFactory().get("/console/")
    request.user = user
    act = ResolvedAction("Close", "/console/incidents/7/close/", "post")

    html = render_to_string(
        "console/partials/_action.html",
        {"act": act, "id": "action-close", "cls": "c-button--danger"},
        request=request,
    )

    assert 'class="c-button c-button--sm c-button--danger"' in html
