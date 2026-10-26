"""No page shows an identifier, a code or raw JSON outside a Details disclosure."""

import re
from html.parser import HTMLParser
from typing import cast

import pytest

from apps.alerts.models import Alert
from apps.console._tests.fixtures import OPS_SLACK_WEBHOOK, PROVIDER_CONFIG, build_world
from apps.console.filters import (
    ANALYSIS_STATUS_CHOICES,
    CHECK_STATUS_CHOICES,
    PREFLIGHT_STATUS_CHOICES,
    REASON_CHOICES,
    SINCE_CHOICES,
)
from apps.console.forms import REFUSED
from apps.intelligence._tests.test_forms import KEY
from apps.intelligence.models import IntelligenceProvider
from apps.notify._tests.test_forms import SECRETS, STORED
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.models import NotificationChannel
from apps.orchestration.describing import REASONS
from config.failures import FAILURE_KINDS
from config.models import APIKey

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")
# "rejected", "timeout" and "unknown" are ordinary words a headline uses ("the provider
# rejected the API key"), so only the codes no sentence would contain are checked.
CODES = {code for code in (*REASONS, *FAILURE_KINDS) if "_" in code}
CHECK_FILTERS = {
    "status": [key for key in CHECK_STATUS_CHOICES if key],
    "checker": ["cpu", "gone_checker"],
    "host": ["hub"],
    "since": [key for key in SINCE_CHOICES if key],
    "q": ["CPU"],
}
PREFLIGHT_FILTERS = {
    "status": [key for key in PREFLIGHT_STATUS_CHOICES if key],
    "since": [key for key in SINCE_CHOICES if key],
}
ANALYSIS_FILTERS = {
    "status": [key for key in ANALYSIS_STATUS_CHOICES if key],
    "provider": ["openai", "claude", "gemini", "local", "retired_provider"],
    "since": [key for key in SINCE_CHOICES if key],
}
VOID = {"br", "img", "input", "meta", "link", "hr", "use"}


class VisibleText(HTMLParser):
    """Text a reader sees without opening Details. Skips script, style and data-details.

    A textarea's text is a form value, stored as written, like an input's ``value``.
    """

    def __init__(self):
        super().__init__()
        self.hidden: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        if self.hidden:
            self.hidden.append(tag)
        elif tag in ("script", "style", "textarea") or "data-details" in dict(attrs):
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def visible(html: str) -> str:
    parser = VisibleText()
    parser.feed(html)
    return " ".join(parser.chunks)


def test_visible_skips_details():
    html = "<p>a<details data-details><details><summary>x</summary>y</details>z</details>b</p>"
    assert visible(html).split() == ["a", "b"]


def test_visible_skips_script_and_style_but_not_void_tags():
    html = '<style>p{}</style><script>x()</script><p>a<br>b<input name="c"></p>'
    assert visible(html).split() == ["a", "b"]


def test_visible_skips_a_textarea_value():
    assert visible("<p>a<textarea>run_pipeline</textarea>b</p>").split() == ["a", "b"]


def _assert_no_leak(url, text):
    assert not UUID.search(text), url
    assert not SNAKE.search(text), (url, SNAKE.findall(text))
    assert "{" not in text, url
    assert not CODES & set(re.findall(r"[a-z_]+", text)), url


def _secret_values() -> list[str]:
    """Every secret value the fixtures store, each string inside a dict on its own."""
    values = [OPS_SLACK_WEBHOOK]
    for config in cast(list[dict], list(STORED.values())):
        for value in (config[key] for key in SECRETS if key in config):
            values += list(value.values()) if isinstance(value, dict) else [value]
    return values


def _filtered(base: str, filters: dict[str, list[str]]) -> list[str]:
    """The list unfiltered, then once per value of each filter."""
    return [base] + [
        f"{base}?{name}={value}" for name, values in filters.items() for value in values
    ]


def _get(client, url, headers):
    response = client.get(url, headers=headers)
    assert response.status_code == 200, url
    return response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("partial", [False, True])
def test_no_page_leaks_identifiers(client, superuser, partial):
    world = build_world()
    channel_pks = [
        NotificationChannel.objects.create(
            name=f"{driver}-off", driver=driver, config=config, is_active=False
        ).pk
        for driver, config in STORED.items()
    ]
    client.force_login(superuser)
    headers = {"HX-Request": "true"} if partial else {}
    alert_pks = list(Alert.objects.values_list("pk", flat=True))
    assert alert_pks
    incident_urls = [f"/console/incidents/{pk}/?all=1" for pk in world.incident_pks]
    urls = (
        ["/console/", "/console/incidents/?status=all", "/console/alerts/?status=all"]
        + incident_urls
        + [f"/console/alerts/{pk}/" for pk in alert_pks]
        + [f"/console/alerts/{pk}/reevaluate/" for pk in alert_pks]
        + ["/console/runs/?status=all", "/console/inbox/", "/console/inbox/?status=processing"]
        + [f"/console/runs/{pk}/" for pk in world.run_pks]
        + ["/console/nodes/", "/console/nodes/?freshness=silent", "/console/policy/"]
        + ["/console/routes/", "/console/routes/new/"]
        + [f"/console/routes/{pk}/{page}" for pk in world.route_pks for page in ("", "delete/")]
        + ["/console/channels/", "/console/channels/new/"]
        + [f"/console/channels/new/?driver={key}" for key in DRIVER_REGISTRY]
        + [
            f"/console/channels/{pk}/{page}"
            for pk in (*world.channel_pks, *channel_pks)
            for page in ("", "delete/")
        ]
        + ["/console/providers/", "/console/providers/new/"]
        + [
            f"/console/providers/new/?provider={key}"
            for key, _ in IntelligenceProvider.PROVIDER_CHOICES
        ]
        + [
            f"/console/providers/{pk}/{page}"
            for pk in world.provider_pks
            for page in ("", "delete/")
        ]
        + ["/console/keys/", "/console/keys/new/"]
        + [f"/console/keys/{pk}/{page}" for pk in world.key_pks for page in ("", "delete/")]
        + [f"/console/nodes/{pk}/" for pk in world.node_pks]
        + [f"/console/nodes/{pk}/policy/" for pk in world.node_pks]
        + [f"/console/nodes/{pk}/reevaluate/" for pk in world.node_pks]
        + [f"/console/nodes/{world.policy_node_pk}/reevaluate/?checker=listening_ports"]
        + [f"/console/runs/?status=failed&reason={reason}" for reason in REASON_CHOICES if reason]
        + ["/console/reports/"]
        + _filtered("/console/checks/", CHECK_FILTERS)
        + [f"/console/checks/{pk}/" for pk in world.check_pks]
        + _filtered("/console/preflight/", PREFLIGHT_FILTERS)
        + [f"/console/preflight/{pk}/" for pk in world.preflight_pks]
        + _filtered("/console/analyses/", ANALYSIS_FILTERS)
        + [f"/console/analyses/{pk}/" for pk in world.analysis_pks]
    )
    html = {url: _get(client, url, headers) for url in urls}
    # The ids are on the page, inside Details, so the check below has something to catch.
    assert UUID.search(html[incident_urls[-1]])
    pages = {url: visible(page) for url, page in html.items()}
    for url, text in pages.items():
        _assert_no_leak(url, text)
    secrets = _secret_values()
    assert len(secrets) == 7
    secrets.append(KEY)
    secrets += list(world.key_tokens)
    secrets += list(APIKey.objects.filter(pk__in=world.key_pks).values_list("key", flat=True))
    for url, page in html.items():
        for secret in secrets:
            assert secret not in page, (url, secret)

    overview = pages["/console/"]
    for fault in (
        "Route pager can't deliver: the channel uses a driver this hub doesn't have",
        "Route escalation can't deliver: the matched route has no active channel",
        "Preflight found 2 errors",
        "1 run is stuck",
        "No node reported in the last 15 minutes",
    ):
        assert fault in overview, fault
    everything = " ".join(pages.values())
    for shown in (
        "Run failed: no route matches this incident",
        "Not delivered: the channel uses a driver this hub doesn't have",
        "AI analysis failed: the provider rejected the API key",
        "Not sent to #ops: Slack is rate limiting this hub",
        "Sent to #ops by Slack",
        "Memory is tight on the hub.",
        "Network reachability could not be checked",
        "Diagnose stalled",
        "Analyze skipped",
        "fired again",
        "Open the runbook",
        "No scorer re-evaluates this alert.",
        "No policy set for CPU usage on db-1.",
        "This alert carries no usable Memory usage value",
        "Run queued",
        "Run in progress",
        "Retry",
        "Mark failed",
        "Drain now",
        "Return stuck runs to the queue",
        "Stuck",
        "Diagnose finished",
        "Unnamed node",
        "High CPU usage on Unnamed node",
        "Saved but not scoring",
        "Not honoured",
        "Edit policy",
        "Re-evaluate open alerts",
        "Allowed ports",
        "would move this alert from critical to warning",
        "No open alerts need re-evaluation.",
        "Applying queues 1 pipeline run.",
        "Severity is Critical",
        "Started by is Incoming webhook",
        "Never reached",
        "Can't deliver",
        "A driver this hub doesn't have",
        "Set. Leave blank to keep it.",
        "These routes will stop delivering",
        "Analysis uses claude-ai.",
        "Turning this on turns off claude-ai.",
        "All endpoints",
        "never used",
        "A provider this hub doesn't have",
        "Analysis falls back to the built-in local analyzer.",
        "Analysis keeps using claude-ai.",
        "Anything still sending this key gets refused.",
        "Preflight found 1 error",
        "From the command line",
        "DB unreachable",
        "Built-in local analyzer",
        "Python 3.12",
        "AI analysis is still running",
        "Rotate logs",
        "of runs finished",
        "Top failure reasons, last 7 days",
        "Other failures",
    ):
        assert shown in everything, shown
    analysis_pages = [page for url, page in html.items() if url.startswith("/console/analyses/")]
    assert any(PROVIDER_CONFIG in page for page in analysis_pages)
    for url, text in pages.items():
        assert PROVIDER_CONFIG not in text, url
    # The templates group is a Details disclosure, so its summary is not visible text.
    assert any("Message templates" in page for page in html.values())


@pytest.mark.django_db
def test_overview_without_a_provider_leaks_nothing(client, superuser):
    build_world()
    IntelligenceProvider.objects.update(is_active=False)
    client.force_login(superuser)

    text = visible(_get(client, "/console/", {}))

    _assert_no_leak("/console/", text)
    assert "No AI provider is active" in text


@pytest.mark.django_db
def test_overview_without_a_channel_leaks_nothing(client, superuser):
    build_world()
    NotificationChannel.objects.update(is_active=False)
    client.force_login(superuser)

    text = visible(_get(client, "/console/", {}))

    _assert_no_leak("/console/", text)
    assert "No active channel, 3 routes can't deliver" in text


@pytest.mark.django_db
def test_the_login_page_leaks_nothing(client):
    page = visible(_get(client, "/console/login/", {}))
    refused = client.post("/console/login/", {"username": "nobody", "password": "x"})
    assert refused.status_code == 200
    refusal = visible(refused.content.decode())

    _assert_no_leak("/console/login/", page)
    _assert_no_leak("/console/login/ refused", refusal)
    assert "Sign in to the console" in page
    assert REFUSED in refusal
