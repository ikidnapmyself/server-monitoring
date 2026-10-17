---
title: "Sysadmin Console PR 6: Alerts"
parent: Plans
---

{% raw %}

# Sysadmin console PR 6: alerts

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Alerts get a console list, a console page and a guarded re-evaluate, and `AlertAdmin` and
`AlertHistoryAdmin` drop to stock and read-only.

**Architecture:** `apps/alerts` owns the wording (`describing.py`) and the guard
(`reeval_existing.py`). `apps/console` owns the reads (`filters.py`, `queries/`) and the pages
(`views/`, `templates/console/`). The alert list follows the incident list's chips, pager and
htmx polling. Alerts are machine state. No operator alert transition exists. Design:
`docs/plans/2026-10-17-sysadmin-console-pr6-alerts-design.md`.

**Tech stack:** Django 5 views and templates, htmx, pytest-django.

**Rules for every task:** absolute imports. No comments that restate code. A line length of 100.
Every wording a describer writes has no UUID, no `snake_case` token and no `{`. Run
`uv run black . && uv run ruff check . --fix` before each commit. End each commit message with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: One node-match rule for both lists

The incident list matches a node by FK or by label. The alert list needs the same rule directly
on `Alert`, so it moves into `apps/console/queries/hosts.py`.

**Files:**
- Modify: `apps/console/queries/hosts.py`
- Modify: `apps/console/queries/incidents.py` (drop `_nodes`, use the shared helpers)
- Test: `apps/console/_tests/queries/test_hosts.py`

**Step 1: Write the failing tests**

Append to `apps/console/_tests/queries/test_hosts.py`:

```python
from apps.alerts.models import Alert, Node
from apps.console.queries.hosts import alert_host, node_choices, on_node


def _bare_alert(key, **fields):
    return Alert.objects.create(
        fingerprint=key, name=key, severity="warning", status="firing", source="cluster", **fields
    )


@pytest.mark.django_db
def test_on_node_matches_the_fk_and_unlinked_labels_only():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    other = Node.objects.create(instance_id="i-db", hostname="db")
    linked = _bare_alert("a", node=node)
    by_label = _bare_alert("b", labels={"instance_id": "i-web"})
    by_hostname = _bare_alert("c", labels={"hostname": "web"})
    _bare_alert("d", node=other, labels={"instance_id": "i-web"})
    _bare_alert("e", labels={"instance": "elsewhere"})

    found = set(Alert.objects.filter(on_node(node.pk, ("i-web", "web"))))

    assert found == {linked, by_label, by_hostname}


@pytest.mark.django_db
def test_node_choices_sort_by_name_and_carry_both_keys():
    Node.objects.create(instance_id="i-2", hostname="Zed")
    Node.objects.create(instance_id="i-1", hostname="")

    assert [(name, keys) for _, name, keys in node_choices()] == [
        ("i-1", ("i-1",)),
        ("Zed", ("i-2", "Zed")),
    ]


def test_alert_host_prefers_the_node_then_the_labels():
    assert alert_host("web", "i-web", {"instance": "x"}) == "web"
    assert alert_host("", "i-web", {}) == "i-web"
    assert alert_host(None, None, {"instance": "x"}) == "x"
    assert alert_host(None, None, None) == ""
```

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_hosts.py -v`
Expected: FAIL with `ImportError: cannot import name 'alert_host'`.

**Step 3: Implement**

In `apps/console/queries/hosts.py`, add `from django.db.models import Q`, import `Node`, and:

```python
def alert_host(hostname: str | None, instance_id: str | None, labels: object) -> str:
    name = hostname or instance_id or instance_key_from_labels(labels)
    return name if isinstance(name, str) else ""


def node_choices() -> tuple[tuple[int, str, tuple[str, ...]], ...]:
    """Every node as its pk, display name and the label values that name it."""
    nodes = [
        (pk, hostname or instance_id, tuple(key for key in (instance_id, hostname) if key))
        for pk, hostname, instance_id in Node.objects.order_by().values_list(
            "pk", "hostname", "instance_id"
        )
    ]
    nodes.sort(key=lambda node: node[1].casefold())
    return tuple(nodes)


def on_node(node_pk: int, node_keys: tuple[str, ...]) -> Q:
    """Alerts about one node.

    Most checker alerts name their machine only in labels, so an unlinked alert also matches
    when its instance label is one of the node's own names.
    """
    named = (
        Q(labels__instance_id__in=node_keys)
        | Q(labels__instance__in=node_keys)
        | Q(labels__hostname__in=node_keys)
    )
    return Q(node_id=node_pk) | Q(node__isnull=True) & named
```

Make `incident_hosts` use `alert_host(hostname, instance_id, labels)` for `name`. In
`apps/console/queries/incidents.py`, delete `_nodes` and the inline `named` Q. Use
`node_choices()` and:

```python
        # A subquery, not a join: a join would cut the alert count down to this node's alerts.
        incidents = incidents.filter(
            Exists(
                Alert.objects.filter(incident=OuterRef("pk")).filter(
                    on_node(criteria.node, node_keys)
                )
            )
        )
```

Also rename `_group` to `chip_group` in `incidents.py` so `queries/alerts.py` can import it, and
widen its `criteria` annotation to `IncidentFilter | AlertFilter` once Task 2 lands. Until then,
leave it as `IncidentFilter`.

**Step 4: Run the console query tests**

Run: `uv run pytest apps/console/_tests/queries/ -v`
Expected: PASS, including every existing incident-list test.

**Step 5: Commit**

```bash
git add apps/console/queries/ apps/console/_tests/queries/test_hosts.py
git commit -m "refactor(console): share the node match between lists"
```

---

### Task 2: `AlertFilter`

**Files:**
- Modify: `apps/console/filters.py`
- Test: `apps/console/_tests/test_filters.py`

**Step 1: Write the failing tests**

Append to `apps/console/_tests/test_filters.py`:

```python
from apps.console.filters import AlertFilter


def _alert_filter(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return AlertFilter.parse(query)


def test_alert_filter_defaults_to_firing():
    assert _alert_filter() == AlertFilter(status="firing")
    assert AlertFilter().statuses() == ("firing",)
    assert AlertFilter(status="all").statuses() == ()


def test_alert_filter_keeps_valid_values_and_drops_the_rest():
    criteria = _alert_filter(
        status="resolved", severity="bogus", node="3", source="grafana", since="7d", page="x"
    )
    assert criteria == AlertFilter(
        status="resolved", node=3, source="grafana", since="7d", dropped=("severity", "page")
    )


def test_alert_filter_search_is_trimmed_capped_and_cleaned():
    criteria = _alert_filter(q="  cpu\x00 " + "x" * 300)
    assert criteria.q.startswith("cpu ")
    assert len(criteria.q) == 255
    assert "\x00" not in criteria.q


def test_alert_filter_querystring_resets_the_page_and_skips_defaults():
    criteria = AlertFilter(q="cpu", page=3)
    assert criteria.querystring(page=4) == "?q=cpu&page=4"
    assert criteria.querystring(status="all") == "?q=cpu&status=all"
    assert AlertFilter().querystring() == ""


def test_alert_filter_without_drops_named_fields():
    criteria = AlertFilter(node=9, source="x").without("node", "source")
    assert criteria == AlertFilter(dropped=("node", "source"))


def test_alert_filter_kept_lists_what_the_search_form_must_carry():
    criteria = AlertFilter(q="cpu", status="all", node=2, page=5)
    assert criteria.kept() == (("status", "all"), ("node", "2"))
```

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/test_filters.py -v`
Expected: FAIL with `ImportError: cannot import name 'AlertFilter'`.

**Step 3: Implement**

In `apps/console/filters.py`, import `AlertStatus`, then extract the shared parsing loop and the
querystring encoding out of `IncidentFilter` so both filters use them:

```python
ALERT_STATUS_CHOICES: dict[str, tuple[str, ...]] = {
    **{status: (status,) for status in AlertStatus.values},
    "all": (),
}
ALERT_FIELDS = ("q", "status", "severity", "node", "source", "since", "page")
SEARCH_LIMIT = 255
SOURCE_LIMIT = 100

Choices = dict[str, Any] | tuple[str, ...] | None


def _parse(params: QueryDict, specs: tuple[tuple[str, Choices], ...]):
    """Valid values by field name, and the names whose value was refused."""
    values: dict[str, str | int] = {}
    dropped = []
    for name, choices in specs:
        raw = params.get(name)
        if raw is None:
            continue
        value = _positive_int(raw) if choices is None else raw if raw in choices else None
        if value is None:
            dropped.append(name)
        else:
            values[name] = value
    return values, dropped


def _text(params: QueryDict, name: str, limit: int) -> str:
    # PostgreSQL refuses a NUL in text.
    return params.get(name, "").replace("\x00", "").strip()[:limit]


def _encode(target, default, fields: tuple[str, ...], changes: dict) -> str:
    """``""`` or ``"?a=b"`` for ``target`` with ``changes`` applied.

    Changing anything other than ``page`` goes back to the first page.
    """
    if changes.keys() - {"page"}:
        changes = {"page": 1, **changes}
    target = replace(target, **changes)
    pairs = [
        (name, getattr(target, name))
        for name in fields
        if getattr(target, name) != getattr(default, name)
    ]
    return f"?{urlencode(pairs)}" if pairs else ""
```

Rewrite `IncidentFilter.parse` as
`values, dropped = _parse(params, (("status", STATUS_CHOICES), ("severity", SEVERITY_CHOICES), ("node", None), ("since", SINCE_CHOICES), ("page", None)))`
followed by `return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]`. Rewrite
`IncidentFilter.querystring` as `return _encode(self, IncidentFilter(), FIELDS, changes)`. Then
add:

```python
@dataclass(frozen=True)
class AlertFilter:
    q: str = ""
    status: str = AlertStatus.FIRING.value
    severity: str = ""
    node: int | None = None
    source: str = ""
    since: str = ""
    page: int = 1
    dropped: tuple[str, ...] = ()

    @classmethod
    def parse(cls, params: QueryDict) -> "AlertFilter":
        values, dropped = _parse(
            params,
            (
                ("status", ALERT_STATUS_CHOICES),
                ("severity", SEVERITY_CHOICES),
                ("node", None),
                ("since", SINCE_CHOICES),
                ("page", None),
            ),
        )
        for name, limit in (("q", SEARCH_LIMIT), ("source", SOURCE_LIMIT)):
            text = _text(params, name, limit)
            if text:
                values[name] = text
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def statuses(self) -> tuple[str, ...]:
        return ALERT_STATUS_CHOICES[self.status]

    def cutoff(self, now: datetime) -> datetime | None:
        window = SINCE_CHOICES[self.since]
        return now - window if window else None

    def querystring(self, **changes: str | int | None) -> str:
        return _encode(self, AlertFilter(), ALERT_FIELDS, changes)

    def without(self, *names: str) -> "AlertFilter":
        default = AlertFilter()
        dropped = tuple(n for n in ALERT_FIELDS if n in names or n in self.dropped)
        return replace(self, **{n: getattr(default, n) for n in names}, dropped=dropped)

    def kept(self) -> tuple[tuple[str, str], ...]:
        """Filters a search submit must resend, since a GET form sends only its own fields."""
        default = AlertFilter()
        return tuple(
            (name, str(getattr(self, name)))
            for name in ("status", "severity", "node", "source", "since")
            if getattr(self, name) != getattr(default, name)
        )
```

Now widen `chip_group`'s `criteria` annotation in `apps/console/queries/incidents.py` to
`IncidentFilter | AlertFilter`.

**Step 4: Run the filter and query tests**

Run: `uv run pytest apps/console/_tests/test_filters.py apps/console/_tests/queries/ -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/filters.py apps/console/queries/incidents.py apps/console/_tests/test_filters.py
git commit -m "feat(console): alert filter"
```

---

### Task 3: The alert list query

**Files:**
- Create: `apps/console/queries/alerts.py`
- Test: `apps/console/_tests/queries/test_alerts.py`

**Step 1: Write the failing tests**

Create `apps/console/_tests/queries/test_alerts.py`:

```python
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.filters import AlertFilter
from apps.console.queries import alerts as alert_queries
from apps.console.queries.alerts import build_alert_list
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db


def _alert(key, **fields):
    fields = {"severity": "warning", "status": "firing", "source": "cluster", **fields}
    return Alert.objects.create(fingerprint=f"fp-{key}", name=key, **fields)


def _titles(listing):
    return [row.title for row in listing.rows]


def test_empty_database_says_none():
    listing = build_alert_list(AlertFilter())
    assert listing.rows == () and listing.empty == "none"


def test_default_shows_firing_newest_first_and_says_none_firing_when_quiet():
    _alert("old")
    _alert("new")
    Alert.objects.filter(name="old").update(updated_at=timezone.now() - timedelta(hours=1))
    assert _titles(build_alert_list(AlertFilter())) == ["new", "old"]

    Alert.objects.update(status="resolved")
    assert build_alert_list(AlertFilter()).empty == "none_firing"


def test_status_chips_count_under_the_other_filters():
    _alert("a", severity="critical")
    _alert("b", severity="critical", status="resolved")
    _alert("c")

    listing = build_alert_list(AlertFilter(severity="critical"))
    status = next(g for g in listing.groups if g.name == "status")

    assert {c.key: c.count for c in status.chips} == {"firing": 1, "resolved": 1, "all": 2}


def test_filters_by_severity_source_since_and_node():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    _alert("linked", node=node)
    _alert("labelled", labels={"instance_id": "i-web"})
    _alert("hook", source="grafana", severity="critical")
    _alert("stale")
    Alert.objects.filter(name="stale").update(updated_at=timezone.now() - timedelta(days=3))

    assert _titles(build_alert_list(AlertFilter(severity="critical"))) == ["hook"]
    assert _titles(build_alert_list(AlertFilter(source="grafana"))) == ["hook"]
    assert "stale" not in _titles(build_alert_list(AlertFilter(since="24h")))
    assert set(_titles(build_alert_list(AlertFilter(node=node.pk)))) == {"linked", "labelled"}


def test_unknown_node_or_source_is_dropped_with_a_notice():
    _alert("a")
    listing = build_alert_list(AlertFilter(node=999, source="nowhere"))
    assert _titles(listing) == ["a"]
    assert listing.notice == "Ignored invalid filter: node, source."


def test_search_matches_name_fingerprint_and_trace_ids():
    incident = Incident.objects.create(subject="s", instance="i", severity="warning")
    _alert("disk full", fingerprint="check:i:disk", trace_id="t-alert")
    _alert("cpu", incident=incident)
    PipelineRun.objects.create(incident=incident, run_id="r", trace_id="t-run")

    assert _titles(build_alert_list(AlertFilter(q="DISK"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="check:i"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="t-alert"))) == ["disk full"]
    assert _titles(build_alert_list(AlertFilter(q="t-run"))) == ["cpu"]
    assert build_alert_list(AlertFilter(q="zzz")).empty == "no_match"


def test_rows_carry_node_incident_and_urls():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = Incident.objects.create(subject="s", instance="i", severity="warning")
    alert = _alert("a", node=node, incident=incident)

    row = build_alert_list(AlertFilter()).rows[0]

    assert (row.node, row.incident_pk) == ("web", incident.pk)
    assert row.url == f"/console/alerts/{alert.pk}/"
    assert row.incident_url == f"/console/incidents/{incident.pk}/"


def test_pages_clamp_and_link(monkeypatch):
    monkeypatch.setattr(alert_queries, "PAGE_SIZE", 1)
    _alert("a")
    _alert("b")

    listing = build_alert_list(AlertFilter(page=9))

    assert (listing.page, listing.pages) == (2, 2)
    assert listing.prev_url == "/console/alerts/?page=1"
    assert listing.next_url == ""
```

Check the `PipelineRun.objects.create` call against the model's required fields. Add any
`trace_id`, `run_id` or `alert_fingerprint` value it needs, using
`apps/console/_tests/fixtures.py::_run` as the model.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.console.queries.alerts'`.

**Step 3: Implement**

Create `apps/console/queries/alerts.py`:

```python
"""Read model for the alert list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import describe_alert
from apps.alerts.models import Alert, AlertStatus
from apps.console.filters import ALERT_STATUS_CHOICES, AlertFilter
from apps.console.queries.hosts import alert_host, node_choices, on_node
from apps.console.queries.incidents import (
    SEVERITY_CHIPS,
    SINCE_CHIPS,
    Choice,
    ChipGroup,
    chip_group,
)
from apps.orchestration.models import PipelineRun
from config.admin_badges import ALERT_STATUS_TONES, SEVERITY_TONES, tone_for

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    ("firing", "Firing", "firing"),
    ("resolved", "Resolved", "resolved"),
    ("all", "All", "all"),
)


@dataclass(frozen=True)
class AlertListRow:
    pk: int
    title: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    node: str
    source: str
    last_seen: datetime
    incident_pk: int | None
    incident_url: str
    url: str


@dataclass(frozen=True)
class AlertList:
    rows: tuple[AlertListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    q: str
    kept: tuple[tuple[str, str], ...]
    notice: str
    empty: str
    url: str


def _matching(criteria: AlertFilter, node_keys: tuple[str, ...]):
    alerts = Alert.objects.all()
    statuses = criteria.statuses()
    if statuses:
        alerts = alerts.filter(status__in=statuses)
    if criteria.severity:
        alerts = alerts.filter(severity=criteria.severity)
    if criteria.source:
        alerts = alerts.filter(source=criteria.source)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        alerts = alerts.filter(updated_at__gte=cutoff)
    if criteria.node is not None:
        alerts = alerts.filter(on_node(criteria.node, node_keys))
    if criteria.q:
        q = criteria.q
        runs = PipelineRun.objects.filter(incident_id=OuterRef("incident_id"), trace_id=q)
        alerts = alerts.filter(
            Q(name__icontains=q) | Q(fingerprint__icontains=q) | Q(trace_id=q) | Exists(runs)
        )
    return alerts


def _status_counts(criteria: AlertFilter, node_keys: tuple[str, ...]) -> dict[str, int]:
    found = _matching(replace(criteria, status="all"), node_keys).aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in AlertStatus.values}
    )
    return {
        key: sum(found.values()) if not statuses else sum(found[s] for s in statuses)
        for key, statuses in ALERT_STATUS_CHOICES.items()
    }


def _rows(alerts, page: int) -> tuple[AlertListRow, ...]:
    start = (page - 1) * PAGE_SIZE
    found = alerts.select_related("node").order_by("-updated_at", "-pk")[start : start + PAGE_SIZE]
    return tuple(
        AlertListRow(
            pk=alert.pk,
            title=describe_alert(alert).headline,
            severity=alert.severity,
            severity_tone=tone_for(SEVERITY_TONES, alert.severity),
            status=alert.status,
            status_tone=tone_for(ALERT_STATUS_TONES, alert.status),
            node=alert_host(
                alert.node.hostname if alert.node else None,
                alert.node.instance_id if alert.node else None,
                alert.labels,
            ),
            source=alert.source,
            last_seen=alert.updated_at,
            incident_pk=alert.incident_id,
            incident_url=(
                reverse("console:incident", args=[alert.incident_id]) if alert.incident_id else ""
            ),
            url=reverse("console:alert", args=[alert.pk]),
        )
        for alert in found
    )


def build_alert_list(criteria: AlertFilter) -> AlertList:
    base = reverse("console:alerts")
    nodes = node_choices()
    node_keys = next((keys for pk, _, keys in nodes if pk == criteria.node), None)
    if criteria.node is not None and node_keys is None:
        criteria = criteria.without("node")
    sources = sorted(
        Alert.objects.order_by().values_list("source", flat=True).distinct(), key=str.casefold
    )
    if criteria.source and criteria.source not in sources:
        criteria = criteria.without("source")
    node_keys = node_keys or ()

    alerts = _matching(criteria, node_keys)
    total = alerts.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        rows, empty = _rows(alerts, page), ""
    elif not Alert.objects.exists():
        rows, empty = (), "none"
    elif replace(criteria, dropped=()) == AlertFilter():
        rows, empty = (), "none_firing"
    else:
        rows, empty = (), "no_match"

    counts = _status_counts(criteria, node_keys)
    groups = [
        chip_group("status", "Status", STATUS_CHIPS, criteria, base, counts),
        chip_group("severity", "Severity", SEVERITY_CHIPS, criteria, base),
    ]
    if nodes:
        any_node: Choice = ("any", "Any node", None)
        node_chips = tuple((str(pk), name, pk) for pk, name, _ in nodes)
        groups.append(chip_group("node", "Node", (any_node, *node_chips), criteria, base))
    if len(sources) > 1:
        any_source: Choice = ("any", "Any source", "")
        source_chips = tuple((s, s, s) for s in sources)
        groups.append(chip_group("source", "Source", (any_source, *source_chips), criteria, base))
    groups.append(chip_group("since", "Since", SINCE_CHIPS, criteria, base))

    return AlertList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=tuple(groups),
        q=criteria.q,
        kept=criteria.kept(),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
```

The `console:alerts` and `console:alert` URL names land in Task 6. Until then, register them in
`apps/console/urls.py` with a placeholder view so `reverse` works in these tests. A
`path("alerts/", OverviewView.as_view(), name="alerts")` and a
`path("alerts/<int:pk>/", OverviewView.as_view(), name="alert")` will do. Task 6 replaces both.

A chip's `key` lands in an element id. A source name comes from a webhook, so the template uses
`forloop.counter` for source chip ids, not the key (Task 6).

**Step 4: Run the tests**

Run: `uv run pytest apps/console/_tests/queries/test_alerts.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/queries/alerts.py apps/console/urls.py apps/console/_tests/queries/test_alerts.py
git commit -m "feat(console): alert list query"
```

---

### Task 4: Re-evaluation in words

The record sentences move from `reeval_display.py` into `apps/alerts/describing.py`, and the
module is deleted. The preview gets its own describers. Skip sentences start naming the checker by
its label, so `disk_inodes` reads "Disk inodes" on every surface.

**Files:**
- Modify: `apps/alerts/describing.py`
- Modify: `apps/alerts/reeval_existing.py` (`preview_reeval` passes `checker_label(checker)`)
- Delete: `apps/alerts/reeval_display.py`
- Move: `apps/alerts/_tests/test_reeval_display.py` to `apps/alerts/_tests/test_describing_reeval.py`
- Modify: `apps/alerts/admin.py` (stop importing `reeval_panel`; the field goes in Task 9)

**Step 1: Port the tests**

`git mv apps/alerts/_tests/test_reeval_display.py apps/alerts/_tests/test_describing_reeval.py`.
In the moved file:
- Import `describe_reeval`, `NEVER` and `UNREADABLE` from `apps.alerts.describing`.
- Assert on the returned `Fact` tuple instead of the HTML. `reeval_panel(x)` returning `NEVER`
  becomes `describe_reeval(x) == ()`. An `assertIn(sentence, panel)` becomes
  `assertIn(sentence, [f.value for f in describe_reeval(x)])`. A label check becomes
  `[f.label for f in ...] == ["At ingest", "On a policy change"]`.
- Delete the two tests that GET `admin:alerts_alert_change`. The console page covers them in
  Task 6.

Then add:

```python
from apps.alerts.describing import (
    REOPEN_WARNING,
    describe_reeval_applied,
    describe_reeval_report,
)
from apps.alerts.reeval_existing import AlertChange, AlertSkip, ReevalReport
from apps.alerts.reevaluation import SkipReason


def _change(**fields):
    fields = {
        "alert": None,
        "old_severity": "critical",
        "old_status": "firing",
        "new_severity": "warning",
        "new_status": "firing",
        "value": 72.04,
        **fields,
    }
    return AlertChange(**fields)


def test_report_words_each_change():
    report = ReevalReport(
        node=None,
        changes=[
            _change(),
            _change(new_status="resolved", new_severity="info"),
            _change(old_status="resolved", new_severity="critical", notify=False),
        ],
    )
    rows = describe_reeval_report(report)
    assert [r.headline for r in rows] == [
        "Would move this alert from critical to warning",
        "Would resolve this alert",
        "Would reopen this alert at critical",
    ]
    assert rows[0].detail == "Its stored value is 72."
    assert rows[2].detail.endswith("Its incident is acknowledged, so nobody is notified.")
    assert [r.tone for r in rows] == ["warning", "ok", "critical"]


@pytest.mark.django_db
def test_report_offers_the_node_only_for_a_skip_an_editor_can_fix():
    node = Node.objects.create(instance_id="i-1", hostname="web")
    report = ReevalReport(
        node=node,
        skips=[
            AlertSkip(alert=None, reason=SkipReason.NO_POLICY, sentence="No policy."),
            AlertSkip(alert=None, reason=SkipReason.NO_SCORER, sentence="No scorer."),
        ],
    )
    fixable, unfixable = describe_reeval_report(report)
    assert fixable.action.intent == "view_node" and fixable.action.params == {"pk": node.pk}
    assert unfixable.action is None and unfixable.tone == "info"


def test_applied_counts_what_changed():
    report = ReevalReport(node=None, changes=[_change(new_status="resolved")])
    assert describe_reeval_applied(report).startswith("Resolved 1, changed severity on 0.")


def test_reopen_warning_has_no_identifiers():
    assert "{" not in REOPEN_WARNING and "_" not in REOPEN_WARNING
```

Fix the `72.04` expectation against `format_value`'s real output when you run it. It rounds to one
decimal place and drops a trailing `.0`, so check whether it prints `72` or `72.0`.

Add a test in `apps/alerts/_tests/` next to the existing `preview_reeval` tests. A skip on a
`disk_inodes` alert has to name the checker "Disk inodes", not `disk_inodes`. Find the existing
preview tests with `grep -rln preview_reeval apps/alerts/_tests` and fix any whose sentence
expected the raw checker name.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_describing_reeval.py -v`
Expected: FAIL with `ImportError`.

**Step 3: Implement**

Move `NEVER`, `UNREADABLE`, `RECORD_LABELS`, `_load`, the four `_*_clause` helpers and
`describe_record` from `reeval_display.py` into `apps/alerts/describing.py`, unchanged. Rename the
local `_load` to `_record` to avoid shadowing. Drop the `format_html_join` import. Then add:

```python
REOPEN_WARNING = (
    "This alert is resolved, but its stored value still breaches the policy. "
    "Applying reopens it and its incident."
)


def describe_reeval(alert: Alert) -> tuple[Fact, ...]:
    """One fact per re-evaluation record the alert carries. Empty when it has none.

    Both records can be present at once, on an alert re-scored at ingest and then by an
    operator, so each fact names the path that wrote it. Nothing here raises, because the
    alert page must open even when a node sent something unreadable.
    """
    annotations = _dict(alert.annotations)
    facts = []
    for key, label in RECORD_LABELS:
        if key in annotations:
            record = _record(annotations[key])
            facts.append(Fact(label, UNREADABLE if record is None else describe_record(record)))
    return tuple(facts)


def _describe_change(change: "AlertChange") -> Description:
    if change.new_status == AlertStatus.RESOLVED:
        headline, tone = "Would resolve this alert", "ok"
    elif change.reopens:
        headline = f"Would reopen this alert at {change.new_severity}"
        tone = tone_for(SEVERITY_TONES, change.new_severity)
    else:
        headline = f"Would move this alert from {change.old_severity} to {change.new_severity}"
        tone = tone_for(SEVERITY_TONES, change.new_severity)
    detail = f"Its stored value is {change.value_display}."
    if not change.notify:
        detail += " Its incident is acknowledged, so nobody is notified."
    return Description(headline=headline, tone=tone, detail=detail)


def _describe_skip(skip: "AlertSkip", node: Node | None) -> Description:
    action = None
    if node is not None and skip.reason in EDITOR_FIXABLE:
        action = Action("Edit the node's policy", intent="view_node", params={"pk": node.pk})
    return Description(headline=skip.sentence, tone="info", action=action)


def describe_reeval_report(report: "ReevalReport") -> tuple[Description, ...]:
    """One description per alert the preview looked at: what would change, or why not."""
    return (
        *(_describe_change(change) for change in report.changes),
        *(_describe_skip(skip, report.node) for skip in report.skips),
    )


def describe_reeval_applied(report: "ReevalReport") -> str:
    runs = report.run_count
    return (
        f"Resolved {report.resolved_count}, changed severity on "
        f"{report.severity_changed_count}. Queued {runs} pipeline run{'s' if runs != 1 else ''}."
    )
```

`apps/alerts/reeval_existing.py` imports from `apps.alerts.services`, and `describing.py` must not
create an import cycle. Import `AlertChange`, `AlertSkip` and `ReevalReport` under
`if TYPE_CHECKING:`. Import `EDITOR_FIXABLE` from `apps.alerts.reevaluation`, which is a leaf.
Import `format_value` from there too, for the clause helpers.

In `reeval_existing.preview_reeval`, pass `checker=checker_label(checker)` to `describe_skip`,
importing `checker_label` from `apps.checkers.checkers`.

Delete `apps/alerts/reeval_display.py`. In `apps/alerts/admin.py`, make `reeval_display` return
`format_html_join("", "<div><b>{}:</b> {}</div>", ((f.label, f.value) for f in describe_reeval(obj))) or NEVER`
so the admin keeps working until Task 9 removes it.

**Step 4: Run the alerts tests**

Run: `uv run pytest apps/alerts -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add -A apps/alerts
git commit -m "refactor(alerts): re-evaluation describes itself"
```

---

### Task 5: The double-submit guard

**Files:**
- Modify: `apps/alerts/reeval_existing.py`
- Test: `apps/alerts/_tests/test_reeval_guard.py`

**Step 1: Write the failing tests**

Create `apps/alerts/_tests/test_reeval_guard.py`. Build a node and a firing CPU alert with stored
metrics that the node's policy re-scores. Copy the setup from the existing `apply_reeval` tests,
which you can find with `grep -rln apply_reeval apps/alerts/_tests`.

```python
import pytest

from apps.alerts.models import Alert, AlertHistory
from apps.alerts.reeval_existing import ReevalScope, StaleReeval, apply_reeval, scope_digest

pytestmark = pytest.mark.django_db


def test_digest_is_stable_until_the_alert_changes(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    before = scope_digest(scope)
    assert scope_digest(scope) == before

    Alert.objects.filter(pk=reevaluable_alert.pk).update(severity="info")

    assert scope_digest(scope) != before


def test_apply_with_a_matching_digest_writes(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    report = apply_reeval(scope, expected=scope_digest(scope))
    assert report.changes
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_submit_is_refused_and_writes_nothing(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    digest = scope_digest(scope)
    apply_reeval(scope, expected=digest)
    written = AlertHistory.objects.count()

    with pytest.raises(StaleReeval):
        apply_reeval(scope, expected=digest)

    assert AlertHistory.objects.count() == written


def test_no_expected_digest_skips_the_check(reevaluable_alert):
    scope = ReevalScope.for_alert(reevaluable_alert)
    assert apply_reeval(scope).changes
```

Define `reevaluable_alert` as a fixture in the same file, built from the existing test setup.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_reeval_guard.py -v`
Expected: FAIL with `ImportError: cannot import name 'StaleReeval'`.

**Step 3: Implement**

In `apps/alerts/reeval_existing.py`, add `import hashlib` and:

```python
class StaleReeval(Exception):
    """The alerts changed between the preview an operator confirmed and the apply."""


def scope_digest(scope: ReevalScope) -> str:
    """What the scope's alerts looked like, as one value a confirm form can carry back."""
    rows = scope.alerts.order_by("pk").values_list("pk", "status", "severity", "updated_at")
    text = "|".join(f"{pk}:{status}:{severity}:{at.isoformat()}" for pk, status, severity, at in rows)
    return hashlib.sha256(text.encode()).hexdigest()
```

Change the signature to `def apply_reeval(scope: ReevalScope, expected: str | None = None)`. Add
one docstring sentence: "With ``expected``, refuses with ``StaleReeval`` unless the scope still
matches that digest." Make these the first lines of the body:

```python
    if expected is not None:
        # Lock first: a second submit waits here, then sees the first one's writes.
        list(scope.alerts.select_for_update())
        if scope_digest(scope) != expected:
            raise StaleReeval
```

**Step 4: Run the alerts tests**

Run: `uv run pytest apps/alerts -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/reeval_existing.py apps/alerts/_tests/test_reeval_guard.py
git commit -m "feat(alerts): re-evaluate refuses a stale confirm"
```

---

### Task 6: Alert list and alert page

**Files:**
- Create: `apps/console/queries/alert.py`
- Create: `apps/console/views/alerts.py`, `apps/console/views/alert.py`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`, `apps/console/intents.py`,
  `apps/console/views/base.py`
- Create: `templates/console/alerts.html`, `templates/console/alert.html`,
  `templates/console/partials/_alerts.html`, `templates/console/partials/_alert_row.html`,
  `templates/console/partials/_alert.html`
- Modify: `templates/console/base.html` (sidebar), `templates/console/partials/_status_icon.html`,
  `static/console/console.css`
- Test: `apps/console/_tests/queries/test_alert.py`, `apps/console/_tests/views/test_alerts.py`,
  `apps/console/_tests/views/test_alert.py`, and updates in `test_intents.py`, `test_base.py` and
  `views/test_incident.py`

**Step 1: Write the failing query tests**

Create `apps/console/_tests/queries/test_alert.py`:

```python
import pytest
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console.queries import alert as alert_query
from apps.console.queries.alert import build_alert_detail

pytestmark = pytest.mark.django_db


def _alert(**fields):
    fields = {
        "fingerprint": "check:i-web:cpu",
        "name": "cpu",
        "severity": "critical",
        "status": "firing",
        "source": "cluster",
        "labels": {"checker": "cpu", "instance_id": "i-web"},
        "raw_payload": {"metrics": {"cpu_percent": 97.1}},
        "trace_id": "t-1",
        "started_at": timezone.now(),
        **fields,
    }
    return Alert.objects.create(**fields)


def test_missing_alert_is_none():
    assert build_alert_detail(999) is None


def test_detail_describes_the_alert_and_its_incident():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = Incident.objects.create(subject="s", instance="i-web", severity="critical")
    alert = _alert(node=node, incident=incident)

    detail = build_alert_detail(alert.pk)

    assert detail.what.headline
    assert detail.node == "web"
    assert detail.incident.pk == incident.pk
    assert detail.incident.url == f"/console/incidents/{incident.pk}/"
    assert {f.label for f in detail.facts} == {"Fingerprint", "Trace ID"}
    assert '"cpu_percent": 97.1' in detail.raw


def test_reevaluate_is_offered_only_to_a_checker_alert_and_an_editor():
    checker = _alert()
    webhook = _alert(fingerprint="hook", labels={}, source="grafana")

    assert [a.intent for a in build_alert_detail(checker.pk, can_reeval=True).do] == [
        "reevaluate_alert"
    ]
    assert build_alert_detail(checker.pk).do == ()
    assert build_alert_detail(webhook.pk, can_reeval=True).do == ()


def test_history_is_newest_first_and_capped(monkeypatch):
    monkeypatch.setattr(alert_query, "HISTORY_LIMIT", 2)
    alert = _alert()
    for event in ("created", "reevaluated", "resolved"):
        AlertHistory.objects.create(alert=alert, event=event, new_status="firing")

    detail = build_alert_detail(alert.pk)

    assert len(detail.history) == 2 and detail.hidden == 1
    assert "cleared" in detail.history[0].what.headline


def test_reeval_facts_come_from_the_describer():
    alert = _alert(annotations={})
    assert build_alert_detail(alert.pk).reeval == ()
```

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_alert.py -v`
Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Implement the query**

Create `apps/console/queries/alert.py`:

```python
"""Read model for one alert's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass
from datetime import datetime

from django.urls import reverse

from apps.alerts.describing import (
    describe_alert,
    describe_history,
    describe_incident,
    describe_reeval,
)
from apps.alerts.models import Alert
from apps.console.queries.hosts import alert_host
from config.admin_badges import (
    ALERT_STATUS_TONES,
    INCIDENT_STATUS_TONES,
    SEVERITY_TONES,
    tone_for,
)
from config.describing import Action, Description, Fact

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class IncidentCard:
    pk: int
    what: Description
    status: str
    status_tone: str
    url: str


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    when: datetime
    what: Description


@dataclass(frozen=True)
class AlertDetail:
    pk: int
    what: Description
    do: tuple[Action, ...]
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    node: str
    source: str
    started_at: datetime | None
    last_seen: datetime
    ended_at: datetime | None
    incident: IncidentCard | None
    history: tuple[HistoryEntry, ...]
    hidden: int
    reeval: tuple[Fact, ...]
    facts: tuple[Fact, ...]
    raw: str
    url: str


def _incident(alert: Alert) -> IncidentCard | None:
    incident = alert.incident
    if incident is None:
        return None
    return IncidentCard(
        pk=incident.pk,
        what=describe_incident(incident),
        status=incident.status,
        status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
        url=reverse("console:incident", args=[incident.pk]),
    )


def _history(alert: Alert, headline: str) -> tuple[tuple[HistoryEntry, ...], int]:
    total = alert.history.count()
    rows = alert.history.order_by("-created_at", "-pk")[:HISTORY_LIMIT]
    entries = []
    for row in rows:
        details = row.details if isinstance(row.details, dict) else {}
        entries.append(
            HistoryEntry(
                id=f"entry-alert_history-{row.pk}",
                when=row.created_at,
                what=describe_history(
                    row.event,
                    alert_headline=headline,
                    old_status=row.old_status,
                    new_status=row.new_status,
                    severity_from=details.get("severity_from"),
                    severity_to=details.get("severity_to"),
                ),
            )
        )
    return tuple(entries), max(0, total - HISTORY_LIMIT)


def build_alert_detail(pk: int, *, can_reeval: bool = False) -> AlertDetail | None:
    """One alert for its page. ``do`` holds only what the caller may do."""
    alert = Alert.objects.select_related("node", "incident").filter(pk=pk).first()
    if alert is None:
        return None
    what = describe_alert(alert)
    history, hidden = _history(alert, what.headline)
    is_checker = bool((alert.labels or {}).get("checker"))
    do = (
        (Action("Re-evaluate against the node's policy", "reevaluate_alert", {"pk": alert.pk}),)
        if can_reeval and is_checker
        else ()
    )
    facts = tuple(
        Fact(label, value, copy=True)
        for label, value in (("Fingerprint", alert.fingerprint), ("Trace ID", alert.trace_id))
        if value
    )
    raw = json.dumps(
        {
            "labels": alert.labels,
            "annotations": alert.annotations,
            "raw_payload": alert.raw_payload,
        },
        indent=2,
        sort_keys=True,
        default=str,
    )
    return AlertDetail(
        pk=alert.pk,
        what=what,
        do=do,
        severity=alert.severity,
        severity_tone=tone_for(SEVERITY_TONES, alert.severity),
        status=alert.status,
        status_tone=tone_for(ALERT_STATUS_TONES, alert.status),
        node=alert_host(
            alert.node.hostname if alert.node else None,
            alert.node.instance_id if alert.node else None,
            alert.labels,
        ),
        source=alert.source,
        started_at=alert.started_at,
        last_seen=alert.updated_at,
        ended_at=alert.ended_at,
        incident=_incident(alert),
        history=history,
        hidden=hidden,
        reeval=describe_reeval(alert),
        facts=facts,
        raw=raw,
        url=reverse("console:alert", args=[alert.pk]),
    )
```

Check `Alert.started_at` nullability in `apps/alerts/models.py` and match the annotation.

**Step 4: Write the failing view tests**

Create `apps/console/_tests/views/test_alerts.py` and `apps/console/_tests/views/test_alert.py`,
following `views/test_incidents.py` and `views/test_incident.py`. Each test:

- `/console/alerts/`: anonymous redirects to login. `bare_staff` gets 403. A user with only
  `alerts.view_alert` gets 200. A row links `/console/alerts/<pk>/`. A webhook alert named
  `MARKUP` renders `ESCAPED`. An htmx request (`HTMX` headers) renders `_alerts.html` only
  (`assert "<html" not in body`). The search form carries a hidden input per `kept()` pair. A
  source chip id is `chip-source-<n>`, never the source name.
- `/console/alerts/<pk>/`: 404 renders `console/not_found.html` with `nav="alerts"`. 200 for
  `view_alert`. The incident card links `/console/incidents/<pk>/`. The Re-evaluate button
  appears for a superuser on a checker alert, and not for a view-only user. Details holds the
  fingerprint.
- The sidebar has an Alerts item with `aria-current="page"` on both pages.

Update the existing tests:

- `apps/console/_tests/test_intents.py:130`: `view_alert` now resolves to `/console/alerts/7/`.
  Add `("reevaluate_alert", {"pk": 7}, "/console/alerts/7/reevaluate/")`.
- `apps/console/_tests/views/test_incident.py:313`: the history link is now
  `reverse("console:alert", args=[alert.pk])`.
- `apps/console/_tests/views/test_base.py:8`: `REFUSAL = "Permission to view this page required."`

**Step 5: Implement the views, routes and intents**

`apps/console/views/base.py`: change the refusal text to
`"Permission to view this page required."`

`apps/console/views/alerts.py`:

```python
from apps.console.filters import AlertFilter
from apps.console.queries.alerts import build_alert_list
from apps.console.views.base import ConsoleView


class AlertListView(ConsoleView):
    template_name = "console/alerts.html"
    partial_template_name = "console/partials/_alerts.html"
    read_permissions = ("alerts.view_alert", "alerts.change_alert")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_alert_list(AlertFilter.parse(self.request.GET))
        context["nav"] = "alerts"
        return context
```

`apps/console/views/alert.py`:

```python
from django.shortcuts import render

from apps.console.queries.alert import build_alert_detail
from apps.console.views.base import ConsoleView

CHANGE_PERMISSION = "alerts.change_alert"


class AlertPageView(ConsoleView):
    read_permissions = ("alerts.view_alert", "alerts.change_alert")

    @property
    def can_reeval(self) -> bool:
        return self.request.user.has_perm(CHANGE_PERMISSION)

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "alerts"}, status=404)


class AlertDetailView(AlertPageView):
    template_name = "console/alert.html"
    partial_template_name = "console/partials/_alert.html"

    def get(self, request, *args, **kwargs):
        alert = build_alert_detail(kwargs["pk"], can_reeval=self.can_reeval)
        if alert is None:
            return self.not_found()
        return self.render_to_response(self.get_context_data(alert=alert, nav="alerts"))
```

Export both from `apps/console/views/__init__.py`. In `apps/console/urls.py`, replace the Task 3
placeholders:

```python
    path("alerts/", AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", AlertDetailView.as_view(), name="alert"),
```

In `apps/console/intents.py`:

```python
    "view_alert": Intent(_record("console:alert"), "get", "alerts.view_alert"),
    "reevaluate_alert": Intent(_record("console:alert-reeval"), "get", "alerts.change_alert"),
```

`console:alert-reeval` lands in Task 7. Register it now as
`path("alerts/<int:pk>/reevaluate/", AlertDetailView.as_view(), name="alert-reeval")`, and Task 7
replaces the view.

**Step 6: Templates**

`templates/console/alerts.html` copies `incidents.html` with the title `Alerts · Console`, the
heading `Alerts` and the include `console/partials/_alerts.html`.

`templates/console/partials/_alerts.html` copies `_incidents.html` with these changes:
- Root `id="alerts"`, and every `#incidents` becomes `#alerts`.
- Above the toolbar, a search form:

```html
  <form class="c-search" role="search" method="get" action="{% url 'console:alerts' %}" hx-get="{% url 'console:alerts' %}" hx-target="#alerts" hx-swap="outerHTML" hx-push-url="true" hx-sync="#alerts:replace">
    {% for name, value in list.kept %}<input type="hidden" name="{{ name }}" value="{{ value }}">{% endfor %}
    <label class="c-sr-only" for="alerts-q">Search alerts</label>
    <input id="alerts-q" class="c-search__input" type="search" name="q" value="{{ list.q }}" maxlength="255" placeholder="Name, fingerprint or trace ID">
    <button id="alerts-search" class="c-button c-button--sm" type="submit">Search</button>
  </form>
```

- For chip ids, use `chip-{{ group.name }}-{{ forloop.counter }}` when `group.name == "source"`,
  and keep `chip-{{ group.name }}-{{ chip.key }}` otherwise.
- The status states nav uses `alert` for firing, `check-circle` for resolved and nothing for
  all.
- The blank states are "No alerts yet." / "Alerts appear here when a node or a webhook reports
  one." for `none`, "Nothing is firing." / "Every alert has cleared." for `none_firing`, and
  "No alerts match these filters." with a clear link to `console:alerts` for `no_match`.
- Rows include `console/partials/_alert_row.html`.

`templates/console/partials/_alert_row.html`:

```html
<li class="c-box__row c-box__row--hover c-row c-status--{{ row.status }}">
  <span class="c-row__icon" title="{{ row.status|capfirst }}">{% include "console/partials/_status_icon.html" with status=row.status %}<span class="c-sr-only">{{ row.status }}</span></span>
  <div class="c-row__main">
    <a id="alert-{{ row.pk }}" href="{{ row.url }}">{{ row.title }}</a>
    <span class="c-label c-tone-{{ row.severity_tone }}">{{ row.severity }}</span>
    <div class="c-row__meta"><span class="c-host{% if not row.node %} c-host--unknown{% endif %}">{% include "console/partials/_octicon.html" with name="server" %}{{ row.node|default:"Unknown host" }}</span> · {{ row.source }} · seen {% include "console/partials/_time.html" with when=row.last_seen %}{% if row.incident_url %} · <a id="alert-{{ row.pk }}-incident" href="{{ row.incident_url }}">Incident #{{ row.incident_pk }}</a>{% endif %}</div>
  </div>
</li>
```

`templates/console/alert.html` copies `incident.html`: the messages loop, then
`<div id="alert" data-poll hx-get="{{ alert.url }}" hx-trigger="every 30s, console:refresh" hx-swap="innerHTML">`
around `{% include "console/partials/_alert.html" %}`.

`templates/console/partials/_alert.html` follows `_incident.html`'s layout:
- Crumb back to `console:alerts`, with the text "Back to alerts".
- A pagehead with `{{ alert.what.headline }}`, a state pill (`c-status--{{ alert.status }}`), the
  host, "Started" and "Last seen".
- **What's wrong**: `_description.html` with `d=alert.what id="alert-what"`.
- **Do this**: when `alert.do` isn't empty, each action through `{% resolved %}` and
  `_action.html`, the first with `cls="c-button--primary"`.
- **Incident**: when `alert.incident`, a box linking `alert.incident.url` with its headline
  and status. Otherwise "Not part of an incident."
- **Re-evaluation**: when `alert.reeval`, a `<dl class="c-facts">` of label and value. Otherwise
  "Never re-evaluated."
- **History**: an `<ol class="c-timeline">` of `alert.history`, each item a `c-entry` with
  `entry.what.headline` and `_time.html`. Add a note "N earlier entries not shown." when
  `alert.hidden` is set.
- **Details**: `{% include "console/partials/_details.html" with facts=alert.facts raw=alert.raw id="alert-details" %}`.
- Sidebar: Severity, Status, Node, Source and Ended (when set).

`templates/console/partials/_status_icon.html`: add `firing` to the `alert` octicon branch, and
`resolved` stays on the `issue-closed` fallback.

`templates/console/base.html`: after the Incidents item, add

```html
      <a class="c-unav__item" href="{% url 'console:alerts' %}"{% if nav == "alerts" %} aria-current="page"{% endif %}>{% include "console/partials/_octicon.html" with name="bell" %}<span>Alerts</span></a>
```

`static/console/console.css`: add `.c-status--firing` beside `.c-status--open`, reusing its
tokens, and add it to the `.c-timeline__badge` group. Add a search style beside `.c-note__form`
that reuses the textarea's tokens:

```css
.c-search { display: flex; gap: var(--space-2); margin-bottom: var(--space-3); }
.c-search__input {
  flex: 1;
  min-width: 0;
  padding: 3px 12px;
  border: 1px solid var(--borderColor-default);
  border-radius: var(--radius);
  background: var(--bgColor-default);
  color: var(--fgColor-default);
  font: 14px/20px var(--fontStack-sans);
}
.c-search__input:focus-visible { border-color: var(--focus-outlineColor); outline-offset: -1px; }
```

**Step 7: Run the console tests**

Run: `uv run pytest apps/console -q`
Expected: PASS. `test_css.py` and `test_templates.py` stay green. If `test_templates.py`'s
`FORBIDDEN` list flags a new template, fix the template rather than the list.

**Step 8: Commit**

```bash
git add apps/console templates/console static/console/console.css
git commit -m "feat(console): alert list and alert page"
```

---

### Task 7: Re-evaluate from the alert page

**Files:**
- Modify: `apps/console/queries/alert.py` (add `build_reeval_preview`)
- Create: `apps/console/views/alert_reeval.py`, `templates/console/alert_reeval.html`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`
- Test: `apps/console/_tests/views/test_alert_reeval.py`, plus a query test in
  `apps/console/_tests/queries/test_alert.py`

**Step 1: Write the failing tests**

Reuse the `reevaluable_alert` setup from Task 5. Move the fixture into a shared
`apps/alerts/_tests/fixtures.py` helper, and import it from both test modules.

```python
import pytest
from django.urls import reverse

from apps.alerts.models import AlertHistory
from apps.alerts.reeval_existing import ReevalScope, scope_digest
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db


def _url(alert):
    return reverse("console:alert-reeval", args=[alert.pk])


def test_preview_writes_nothing(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Would " in body
    assert 'name="digest"' in body
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_view_only_user_sees_the_preview_without_a_button(client, view_alert_user, reevaluable_alert):
    client.force_login(view_alert_user)
    body = client.get(_url(reevaluable_alert)).content.decode()
    assert "Would " in body and 'id="reeval-apply"' not in body


def test_apply_writes_history_enqueues_and_redirects(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    digest = scope_digest(ReevalScope.for_alert(reevaluable_alert))

    response = client.post(_url(reevaluable_alert), {"digest": digest})

    assert response.status_code == 303
    assert response["Location"] == reverse("console:alert", args=[reevaluable_alert.pk])
    assert AlertHistory.objects.filter(alert=reevaluable_alert).exists()
    assert PipelineRun.objects.filter(incident=reevaluable_alert.incident).exists()


def test_a_stale_digest_refuses_and_writes_nothing(client, superuser, reevaluable_alert):
    client.force_login(superuser)

    response = client.post(_url(reevaluable_alert), {"digest": "stale"})

    assert response.status_code == 409
    assert "This alert changed since you looked." in response.content.decode()
    assert not AlertHistory.objects.filter(alert=reevaluable_alert).exists()


def test_a_replayed_post_is_a_no_op(client, superuser, reevaluable_alert):
    client.force_login(superuser)
    digest = scope_digest(ReevalScope.for_alert(reevaluable_alert))
    client.post(_url(reevaluable_alert), {"digest": digest})
    written = AlertHistory.objects.count()

    assert client.post(_url(reevaluable_alert), {"digest": digest}).status_code == 409
    assert AlertHistory.objects.count() == written


def test_post_needs_change_permission(client, view_alert_user, reevaluable_alert):
    client.force_login(view_alert_user)
    assert client.post(_url(reevaluable_alert), {"digest": "x"}).status_code == 403


def test_missing_alert_is_404(client, superuser):
    client.force_login(superuser)
    assert client.get(reverse("console:alert-reeval", args=[999])).status_code == 404
```

Add a `view_alert_user` fixture to `apps/console/_tests/conftest.py`, modelled on `staff`, with the
`alerts.view_alert` permission. Add a query test showing that `build_reeval_preview` sets
`warning == REOPEN_WARNING` for a resolved alert whose stored value still breaches the policy. It
also sets `can_apply` to False for an alert with no registered node.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/views/test_alert_reeval.py -v`
Expected: FAIL. The preview has no `Would ` row yet, because the placeholder renders the alert
page.

**Step 3: Implement the preview query**

Append to `apps/console/queries/alert.py`:

```python
from apps.alerts.describing import REOPEN_WARNING, describe_reeval_report
from apps.alerts.reeval_existing import ReevalScope, preview_reeval, scope_digest


@dataclass(frozen=True)
class ReevalPreview:
    pk: int
    what: Description
    rows: tuple[Description, ...]
    warning: str
    can_apply: bool
    digest: str
    alert_url: str
    url: str


def build_reeval_preview(pk: int) -> ReevalPreview | None:
    alert = Alert.objects.filter(pk=pk).first()
    if alert is None:
        return None
    scope = ReevalScope.for_alert(alert)
    # Before the preview: if the alert moves in between, the apply refuses rather than
    # writing a change the operator never saw.
    digest = scope_digest(scope)
    report = preview_reeval(scope)
    return ReevalPreview(
        pk=alert.pk,
        what=describe_alert(alert),
        rows=describe_reeval_report(report),
        warning=REOPEN_WARNING if report.reopened_count else "",
        can_apply=bool(report.changes) and report.node is not None,
        digest=digest,
        alert_url=reverse("console:alert", args=[alert.pk]),
        url=reverse("console:alert-reeval", args=[alert.pk]),
    )
```

Move the new imports to the top of the module.

**Step 4: Implement the view**

`apps/console/views/alert_reeval.py`:

```python
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect

from apps.alerts.describing import describe_reeval_applied
from apps.alerts.models import Alert
from apps.alerts.reeval_existing import ReevalScope, StaleReeval, apply_reeval
from apps.console.queries.alert import build_reeval_preview
from apps.console.views.alert import AlertPageView
from apps.console.views.incident import Notice

STALE = "This alert changed since you looked. Here is what re-evaluating does now."


class AlertReevalView(AlertPageView):
    """Preview, then apply, one alert's re-score. The rules live in ``apply_reeval``."""

    template_name = "console/alert_reeval.html"
    partial_template_name = "console/alert_reeval.html"

    def landing_path(self) -> str:
        return self.request.path

    def render_preview(self, pk: int, notice: Notice | None = None, status: int = 200):
        preview = build_reeval_preview(pk)
        if preview is None:
            return self.not_found()
        context = self.get_context_data(
            preview=preview, can_reeval=self.can_reeval, nav="alerts", notice=notice
        )
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_preview(kwargs["pk"])

    def post(self, request, *args, **kwargs):
        if not self.can_reeval:
            return HttpResponseForbidden("Permission to change alerts required.")
        alert = Alert.objects.filter(pk=kwargs["pk"]).first()
        if alert is None:
            return self.not_found()
        try:
            report = apply_reeval(
                ReevalScope.for_alert(alert), expected=request.POST.get("digest", "")
            )
        except StaleReeval:
            return self.render_preview(alert.pk, Notice(STALE, "warning"), status=409)
        messages.success(request, describe_reeval_applied(report))
        return HttpResponseRedirect(build_reeval_preview(alert.pk).alert_url, status=303)
```

Replace the redirect target with `reverse("console:alert", args=[alert.pk])`, so the success path
doesn't run the preview twice. Export the view from `views/__init__.py`, and point the
`alert-reeval` route at `AlertReevalView.as_view()`.

**Step 5: Template**

`templates/console/alert_reeval.html` extends `console/base.html`, with the title
`Re-evaluate · {{ preview.what.headline }} · Console`:

```html
{% load console %}
{% if notice %}{% include "console/partials/_flash.html" with tone=notice.tone text=notice.text %}{% endif %}
<a id="reeval-back" class="c-crumb" href="{{ preview.alert_url }}">{% include "console/partials/_octicon.html" with name="chevron-right" %}Back to the alert</a>
<div class="c-pagehead"><div class="c-pagehead__top"><h1 class="c-pagehead__title">Re-evaluate {{ preview.what.headline }}</h1></div></div>
<section class="c-box c-comment">
  <h2 class="c-box__header c-box__title">What re-evaluating does</h2>
  <div class="c-box__body">
    {% for row in preview.rows %}{% include "console/partials/_description.html" with d=row id="reeval-row-"|add:forloop.counter %}{% endfor %}
    {% if preview.warning %}{% include "console/partials/_flash.html" with tone="warning" text=preview.warning %}{% endif %}
  </div>
</section>
{% if preview.can_apply and can_reeval %}
<form method="post" action="{{ preview.url }}">
  {% csrf_token %}
  <input type="hidden" name="digest" value="{{ preview.digest }}">
  <button id="reeval-apply" class="c-button c-button--primary" type="submit">Apply re-evaluation</button>
  <a id="reeval-cancel" class="c-button" href="{{ preview.alert_url }}">Cancel</a>
</form>
{% endif %}
```

Wrap that markup in `{% block content %}`. `forloop.counter` is an int, and `add` on a string
fails, so build the id with `{% with %}` or a `stringformat` filter. Check that the rendered id
reads `reeval-row-1`.

**Step 6: Run the console and alerts tests**

Run: `uv run pytest apps/console apps/alerts -q`
Expected: PASS.

**Step 7: Commit**

```bash
git add apps/console apps/alerts/_tests templates/console/alert_reeval.html
git commit -m "feat(console): re-evaluate an alert with a stale-confirm guard"
```

---

### Task 8: The guard test covers alert pages

**Files:**
- Modify: `apps/console/_tests/test_guard.py`

**Step 1: Extend the test**

In `test_no_page_leaks_identifiers`, add the alert list, every alert page and every alert's
re-evaluate page:

```python
    alert_pks = list(Alert.objects.values_list("pk", flat=True))
    urls = (
        ["/console/", "/console/incidents/?status=all", "/console/alerts/?status=all"]
        + [f"/console/incidents/{pk}/?all=1" for pk in world.incident_pks]
        + [f"/console/alerts/{pk}/" for pk in alert_pks]
        + [f"/console/alerts/{pk}/reevaluate/" for pk in alert_pks]
    )
```

Import `Alert`. Keep the `assert UUID.search(html[...])` line pointed at an incident page by
finding the last incident URL explicitly, not `urls[-1]`.

**Step 2: Run it**

Run: `uv run pytest apps/console/_tests/test_guard.py -v`
Expected: PASS. If a page leaks, fix the describer or the template, never the guard. Likely
leaks are a raw checker name in a skip sentence (Task 4 should have fixed it), a raw `source`
value, and a history event name. Source text is passthrough per `AGENTS.md` rule 14. The guard
test's fixture sources (`grafana`, `cluster`) are plain words, so nothing should trip.

**Step 3: Commit**

```bash
git add apps/console/_tests/test_guard.py
git commit -m "test(console): alert pages leak no identifiers"
```

---

### Task 9: Cut `AlertAdmin` and `AlertHistoryAdmin` to stock

**Files:**
- Modify: `apps/alerts/admin.py`
- Delete: `apps/alerts/_tests/test_alert_admin_reeval.py`
- Modify: `apps/alerts/_tests/test_admin.py`, `apps/orchestration/_tests/test_admin.py`,
  `apps/alerts/policy_overview.py:133` (docstring)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing tests**

Add to `apps/alerts/_tests/test_admin.py`:

```python
class TestAlertAdminIsStock(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(self.user)
        self.alert = Alert.objects.create(
            fingerprint="fp", name="a", severity="warning", status="firing", source="cluster"
        )

    def test_alert_admin_is_read_only_with_no_actions(self):
        model_admin = admin.site._registry[Alert]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, self.alert)
        assert "resolve_selected" not in model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_history_admin_is_read_only(self):
        model_admin = admin.site._registry[AlertHistory]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)

    def test_change_page_still_opens_for_a_superuser(self):
        url = reverse("admin:alerts_alert_change", args=[self.alert.pk])
        assert self.client.get(url).status_code == 200
```

Delete these, which test what's being removed: `test_resolve_selected_alerts`, the alert half of
`TestJsonPrettyDisplay`, `test_alert_journey_links_incident_and_shows_trace`,
`test_alert_journey_shows_inbox_when_no_incident`, `TestAlertHistoryAdminReadability` and all of
`apps/alerts/_tests/test_alert_admin_reeval.py`. Keep
`apps/orchestration/_tests/test_admin.py:186`'s fingerprint search, because stock search keeps
`fingerprint`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_admin.py -v`
Expected: FAIL on `has_change_permission` and `change_actions`.

**Step 3: Implement**

Replace `AlertAdmin` and `AlertHistoryAdmin` in `apps/alerts/admin.py`:

```python
@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Read-only. Operators read alerts in the console, and nobody resolves one by hand."""

    list_display = ["name", "severity", "status", "source", "node", "incident", "received_at"]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "trace_id"]
    list_select_related = ["node", "incident"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    """Read-only audit rows, written by the code that changes an alert."""

    list_display = ["alert", "event", "old_status", "new_status", "created_at"]
    list_filter = ["event"]
    search_fields = ["alert__name"]
    list_select_related = ["alert"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
```

Then delete what only the old classes used: `AlertHistoryInline` (if only `AlertAdmin` used it),
`alert_reeval_intro`, and any import that goes unused (`reeval_panel`, `describe_reeval`,
`ReevalScope` and `apply_reeval` if `NodeAdmin` doesn't use them, and `TemplateResponse` if
nothing else does). Let `uv run ruff check apps/alerts/admin.py` list them. Keep `node_label`,
`AlertInline` and `PipelineRunInline`, because `IncidentAdmin` and `NodeAdmin` still use them.

Check `templates/admin/alerts/` for an alert-only template, such as a custom change form, and
delete it if nothing references it. `templates/admin/alerts/node/reevaluate_confirm.html` stays
for `NodeAdmin`.

Update the `policy_overview.py:133` docstring. The sentence about
`AlertAdmin.resolve_selected` writing no history is stale, because no operator alert resolve
exists. Reword or drop that clause without changing the code.

**Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. Fix any admin test that clicked through the removed Alert actions.
`config/_tests/test_dashboard_render.py:99` only reverses the changelist, which still exists.

**Step 5: Commit**

```bash
git add -A apps/alerts apps/orchestration/_tests/test_admin.py templates/admin
git commit -m "feat(alerts)!: alert admin is stock and read-only

BREAKING CHANGE: the admin's Resolve selected alerts action and per-alert
Re-evaluate button are gone. Re-evaluate lives on the console alert page."
```

---

### Task 10: Docs

**Files:**
- Modify: `apps/alerts/AGENTS.md`, `apps/console/AGENTS.md`
- Modify: `docs/plans/2026-10-17-sysadmin-console-pr6-alerts-design.md` (digest naming only: this
  PR's own design, not yet merged)

**Step 1: Edit**

In `apps/alerts/AGENTS.md`:
- Add a short "Alerts are machine state" section after "Role in the pipeline". Only producers
  change an alert's status: node push, webhook, `check_health`, re-evaluate. Operators act on
  incidents. No operator alert transition exists, and none should be added. Flapping belongs to a
  future incident snooze.
- Under "Re-evaluation surfaces", replace the per-alert admin action with the console page
  `/console/alerts/<pk>/reevaluate/` and the `scope_digest` / `StaleReeval` guard. Keep the node
  admin action, which stays until PR 8.
- Under "Key modules", drop `reeval_display.py`. Say that `describing.py` holds
  `describe_reeval`, `describe_reeval_report` and `describe_reeval_applied`.
- Under "Django Admin expectations", say that `AlertAdmin` and `AlertHistoryAdmin` are stock
  and read-only.

In `apps/console/AGENTS.md`, under "Rules", add the alert pages, the `AlertFilter` fields, the
shared node match in `queries/hosts.py`, and the rule that a confirm form carries a
`scope_digest` and the apply refuses a stale one.

In the design doc, replace `alert_state_digest(alert)` with `scope_digest(scope)`. It's a hash over
the scope's alerts, so PR 8's node-wide confirm reuses it unchanged.

**Step 2: Verify everything**

```bash
uv run black . --check && uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report --include="apps/console/*,apps/alerts/describing.py,apps/alerts/reeval_existing.py,apps/alerts/admin.py"
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py check
```

Expected: all green, with 100% branch coverage on the changed files.

**Step 3: Commit**

```bash
git add apps/alerts/AGENTS.md apps/console/AGENTS.md docs/plans/2026-10-17-sysadmin-console-pr6-alerts-design.md
git commit -m "docs: console alerts; alerts are machine state"
```

{% endraw %}
