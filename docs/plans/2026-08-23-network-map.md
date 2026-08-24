---
title: "Network Map Implementation Plan"
parent: Plans
---

{% raw %}

# Network Map Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** An admin page (`/admin/map/`) drawing every `PipelineDefinition` lane in match order with its reach (shadowed / never-matches / inactive) and delivery state (bound / recording-only / no_channel / no_driver).

**Architecture:** Pure read-time projection. `config/netmap.py` computes everything (`get_map_context()`); a `get_urls()` override on `MonitoringAdminSite` serves it through a dumb template. Shadow detection is symbolic subsumption over the four match ops, failing toward "not shadowed". No new models, apps, endpoints, or JS.

**Design:** `docs/plans/2026-08-23-network-map-design.md`

**Tech stack:** Django admin views, server-rendered template, pytest-django.

---

## Context the executor needs

- `PipelineDefinition` (`apps/orchestration/models.py`): fields `name`, `match` (JSON list of `{"field","op","value"}` conds, AND, empty = catch-all), `priority` (lower wins? — **verify**: read `resolve_pipeline` ordering in `apps/orchestration/orchestrator.py` and mirror it exactly in the builder), `stages`, `tags`, `is_active`, `channel` FK. Helpers to reuse, never re-derive: `routable_stages()`, `routed_channel()`, `delivery_gap()`, and `matches()` (its fail-closed rules define "never matches": non-dict cond, unknown op, `in`/`not-in` with non-list value).
- Seed provenance: `apps/orchestration/seeding.py` — `SEED_SHAPE_KEY = "seed_shape"` in `tags` marks seed-shaped rows.
- Admin site: `config/admin.py:35` `MonitoringAdminSite` (installed as default site via `config/apps.py`); it has no `get_urls` override yet. Use `self.admin_view()` for auth.
- Dashboard template pattern: `templates/admin/dashboard.html` (extends `admin/index.html`, inline `<style>`). The map page extends `admin/base_site.html` instead — it is its own page, not the index.
- Label facts: `field` may be `"label:foo"`; subsumption treats field names opaquely, so nothing special is needed.
- Verify after every task: `uv run pytest config/_tests/test_netmap.py -v`, and at the end `uv run black . && uv run ruff check . --fix && uv run coverage run -m pytest && uv run coverage report` (100% branch on changed files), `uv run python manage.py check`.
- Commit after every green step. Absolute imports. Line length 100.

---

## Task 1: Subsumption core — `_cond_implied` and `_lane_shadows`

**Files:**
- Create: `config/netmap.py`
- Create: `config/_tests/test_netmap.py`

**Step 1: failing tests**

```python
"""Tests for the network-map projection (config/netmap.py)."""

import pytest

from config.netmap import _cond_implied, _lane_shadows


def cond(field, op, value):
    return {"field": field, "op": op, "value": value}


class TestCondImplied:
    """_cond_implied(b_conds, a_cond): do B's conditions on a field prove A's?"""

    @pytest.mark.parametrize(
        "b,a,expected",
        [
            # is / is
            ([cond("source", "is", "cluster")], cond("source", "is", "cluster"), True),
            ([cond("source", "is", "cluster")], cond("source", "is", "grafana"), False),
            # is ⇒ in / is-not / not-in
            ([cond("sev", "is", "high")], cond("sev", "in", ["high", "critical"]), True),
            ([cond("sev", "is", "high")], cond("sev", "in", ["low"]), False),
            ([cond("sev", "is", "high")], cond("sev", "is-not", "low"), True),
            ([cond("sev", "is", "high")], cond("sev", "is-not", "high"), False),
            ([cond("sev", "is", "high")], cond("sev", "not-in", ["low", "info"]), True),
            ([cond("sev", "is", "high")], cond("sev", "not-in", ["high"]), False),
            # in ⇒ ...
            ([cond("sev", "in", ["high"])], cond("sev", "is", "high"), True),
            ([cond("sev", "in", ["high", "critical"])], cond("sev", "is", "high"), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "in", ["a", "b", "c"]), True),
            ([cond("sev", "in", ["a", "d"])], cond("sev", "in", ["a", "b", "c"]), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "is-not", "c"), True),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "is-not", "a"), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "not-in", ["c", "d"]), True),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "not-in", ["b"]), False),
            # negation ⇒ negation
            ([cond("sev", "is-not", "low")], cond("sev", "is-not", "low"), True),
            ([cond("sev", "is-not", "low")], cond("sev", "is-not", "high"), False),
            ([cond("sev", "not-in", ["a", "b"])], cond("sev", "not-in", ["a"]), True),
            ([cond("sev", "not-in", ["a"])], cond("sev", "not-in", ["a", "b"]), False),
            ([cond("sev", "is-not", "a")], cond("sev", "not-in", ["a"]), True),
            ([cond("sev", "is-not", "a")], cond("sev", "not-in", ["a", "b"]), False),
            ([cond("sev", "not-in", ["a", "b"])], cond("sev", "is-not", "a"), True),
            ([cond("sev", "not-in", ["b"])], cond("sev", "is-not", "a"), False),
            # open-domain: negation on B cannot prove a positive on A
            ([cond("sev", "is-not", "low")], cond("sev", "is", "high"), False),
            ([cond("sev", "not-in", ["low"])], cond("sev", "in", ["high"]), False),
            # B has no condition on the field → unprovable
            ([], cond("sev", "is", "high"), False),
            ([cond("other", "is", "x")], cond("sev", "is", "high"), False),
            # any B condition on the field proving A suffices
            (
                [cond("sev", "is-not", "x"), cond("sev", "is", "high")],
                cond("sev", "in", ["high"]),
                True,
            ),
            # default op is "is" (mirrors matches())
            ([{"field": "sev", "value": "high"}], cond("sev", "is", "high"), True),
        ],
    )
    def test_implication_table(self, b, a, expected):
        assert _cond_implied(b, a) is expected


class TestLaneShadows:
    def test_empty_match_shadows_everything(self):
        assert _lane_shadows([], [cond("sev", "is", "high")]) is True

    def test_nonempty_cannot_shadow_catch_all(self):
        assert _lane_shadows([cond("sev", "is", "high")], []) is False

    def test_every_a_cond_must_be_proven(self):
        a = [cond("source", "is", "cluster"), cond("sev", "is", "high")]
        b = [cond("source", "is", "cluster")]
        assert _lane_shadows(a, b) is False

    def test_superset_by_conditions(self):
        a = [cond("source", "is", "cluster")]
        b = [cond("source", "is", "cluster"), cond("sev", "is", "high")]
        assert _lane_shadows(a, b) is True
```

**Step 2: run — expect ImportError.** `uv run pytest config/_tests/test_netmap.py -v`

**Step 3: implement in `config/netmap.py`**

```python
"""Network map: a read-time projection of the routing table.

Everything here is computed from ``PipelineDefinition`` + ``NotificationChannel``
at request time — no storage, no caching, no side effects. Shadow detection is
symbolic subsumption over the four match ops; anything unprovable is drawn as a
normal lane (fail toward "not shadowed").

Design: docs/plans/2026-08-23-network-map-design.md
"""


def _as_set(value) -> set | None:
    return set(value) if isinstance(value, (list, tuple)) else None


def _cond_implied(b_conds: list[dict], a_cond: dict) -> bool:
    """True if some condition of lane B provably implies A's condition ``a_cond``.

    B's conditions are AND-ed, so one implying condition on the same field is
    enough. Every unlisted pairing is unprovable → False. The ``op`` default and
    value shapes mirror ``PipelineDefinition.matches()`` exactly.
    """
    field = a_cond.get("field")
    a_op, a_val = a_cond.get("op", "is"), a_cond.get("value")
    for b in b_conds:
        if b.get("field") != field:
            continue
        b_op, b_val = b.get("op", "is"), b.get("value")
        if b_op == "is":
            if a_op == "is" and b_val == a_val:
                return True
            if a_op == "in" and (s := _as_set(a_val)) is not None and b_val in s:
                return True
            if a_op == "is-not" and b_val != a_val:
                return True
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and b_val not in s:
                return True
        elif b_op == "in" and (bs := _as_set(b_val)) is not None:
            if a_op == "is" and bs == {a_val}:
                return True
            if a_op == "in" and (s := _as_set(a_val)) is not None and bs <= s:
                return True
            if a_op == "is-not" and a_val not in bs:
                return True
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and not (bs & s):
                return True
        elif b_op == "is-not":
            if a_op == "is-not" and b_val == a_val:
                return True
            if a_op == "not-in" and _as_set(a_val) == {b_val}:
                return True
        elif b_op == "not-in" and (bs := _as_set(b_val)) is not None:
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and s <= bs:
                return True
            if a_op == "is-not" and a_val in bs:
                return True
    return False


def _lane_shadows(a_match: list, b_match: list) -> bool:
    """True if lane A (earlier, active) provably matches everything lane B matches."""
    return all(_cond_implied(b_match, a_cond) for a_cond in a_match)
```

**Step 4: run — expect all PASS.**

**Step 5: commit** — `feat(config): subsumption core for the network map`

---

## Task 2: Lane classification — `_never_matches` and `_annotate_shadows`

**Files:**
- Modify: `config/netmap.py`
- Modify: `config/_tests/test_netmap.py`

**Step 1: failing tests**

```python
from config.netmap import _annotate_shadows, _never_matches


class TestNeverMatches:
    """Static mirror of matches() fail-closed rules."""

    @pytest.mark.parametrize(
        "match,expected",
        [
            ([], False),
            ([cond("sev", "is", "high")], False),
            (["not-a-dict"], True),
            ([cond("sev", "frobnicate", "x")], True),
            ([cond("sev", "in", "high")], True),        # membership needs a list
            ([cond("sev", "not-in", "high")], True),
            (None, False),                               # match may be None/junk JSON
            ("junk", False),                             # non-list treated as no conds
        ],
    )
    def test_table(self, match, expected):
        assert _never_matches(match) is expected


def lane(name, match, active=True, priority=100):
    """Stand-in with the attributes _annotate_shadows reads."""
    return type("L", (), {"name": name, "match": match, "is_active": active,
                          "priority": priority})()


class TestAnnotateShadows:
    def test_earlier_catch_all_shadows_later(self):
        lanes = [lane("catch-all", []), lane("cluster", [cond("source", "is", "cluster")])]
        assert _annotate_shadows(lanes) == {"cluster": "catch-all"}

    def test_first_shadower_named(self):
        lanes = [
            lane("a", [cond("s", "in", ["x", "y"])]),
            lane("b", [cond("s", "in", ["x", "y", "z"])]),
            lane("c", [cond("s", "is", "x")]),
        ]
        # c is shadowed by a (first prover wins); b is not shadowed by a
        assert _annotate_shadows(lanes) == {"c": "a"}

    def test_inactive_lane_does_not_shadow(self):
        lanes = [lane("off", [], active=False), lane("cluster", [cond("s", "is", "x")])]
        assert _annotate_shadows(lanes) == {}

    def test_inactive_and_never_match_lanes_not_marked_shadowed(self):
        lanes = [
            lane("catch-all", []),
            lane("off", [cond("s", "is", "x")], active=False),
            lane("broken", [cond("s", "bad-op", "x")]),
        ]
        assert _annotate_shadows(lanes) == {}

    def test_never_matching_lane_does_not_shadow(self):
        lanes = [lane("broken", [cond("s", "bad-op", "x")]), lane("b", [cond("s", "is", "x")])]
        assert _annotate_shadows(lanes) == {}
```

**Step 2: run — expect ImportError/failures.**

**Step 3: implement**

```python
_MATCH_OPS = ("is", "is-not", "in", "not-in")


def _never_matches(match) -> bool:
    """Static mirror of ``PipelineDefinition.matches()`` fail-closed rules.

    A condition that can never hold — non-dict, unknown op, membership without a
    list — makes the whole lane unmatchable. A non-list ``match`` iterates as
    empty in ``matches()`` (catch-all), so it is NOT never-matching.
    """
    if not isinstance(match, list):
        return False
    for c in match:
        if not isinstance(c, dict):
            return True
        op = c.get("op", "is")
        if op not in _MATCH_OPS:
            return True
        if op in ("in", "not-in") and not isinstance(c.get("value"), (list, tuple)):
            return True
    return False


def _annotate_shadows(lanes) -> dict[str, str]:
    """Map lane name → name of the earlier active lane that provably shadows it.

    ``lanes`` must already be in match (priority) order. Inactive and
    never-matching lanes neither shadow nor get marked shadowed — they have
    their own states on the map.
    """
    shadowed: dict[str, str] = {}
    candidates: list = []  # earlier lanes that can actually win traffic
    for b in lanes:
        if b.is_active and not _never_matches(b.match):
            for a in candidates:
                if _lane_shadows(a.match or [], b.match or []):
                    shadowed[b.name] = a.name
                    break
            candidates.append(b)
    return shadowed
```

Note: a shadowed lane still joins ``candidates`` — it is itself unreachable, but
traffic it would have taken is taken by its shadower, and naming the *first*
prover keeps the message actionable.

**Step 4: run — PASS. Step 5: commit** — `feat(config): shadow and never-match classification`

---

## Task 3: `get_map_context()`

**Files:**
- Modify: `config/netmap.py`
- Modify: `config/_tests/test_netmap.py`

**Step 1: failing tests** (DB-backed; follow the fixture style of `config/_tests/test_dashboard_readiness.py` — check how it creates `PipelineDefinition` / `NotificationChannel` rows and copy that idiom, including any required fields).

```python
# imports at top of file:
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from config.netmap import get_map_context


@pytest.mark.django_db
class TestGetMapContext:
    def _lane(self, name, priority, **kw):
        return PipelineDefinition.objects.create(name=name, priority=priority, **kw)

    def test_lanes_in_match_order(self):
        self._lane("late", priority=200, match=[])
        self._lane("early", priority=10, match=[])
        names = [c["name"] for c in get_map_context()["lanes"]]
        assert names == ["early", "late"]  # mirror resolve_pipeline ordering exactly

    def test_card_fields_bound_lane(self):
        ch = NotificationChannel.objects.create(
            name="ops", driver="email", is_active=True, config={}
        )
        self._lane(
            "cluster", priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
            stages=["check", "analyze", "notify"], channel=ch,
            tags={"seed_shape": "record-only"},
        )
        card = get_map_context()["lanes"][0]
        assert card["state"] == "ok"
        assert card["conditions"] == ["source is cluster"]
        assert card["stages"] == ["check", "analyze", "notify"]
        assert card["delivery"] == {"state": "bound", "channel": "ops"}
        assert card["seeded"] is True
        assert card["admin_url"].endswith(f"/change/")

    def test_delivery_states(self):
        self._lane("rec", priority=1, match=[{"field": "s", "op": "is", "value": "a"}],
                   stages=["check"])
        self._lane("gap", priority=2, match=[{"field": "s", "op": "is", "value": "b"}],
                   stages=["notify"])
        ch = NotificationChannel.objects.create(
            name="ghost", driver="not-a-driver", is_active=True, config={}
        )
        self._lane("bad", priority=3, match=[{"field": "s", "op": "is", "value": "c"}],
                   stages=["notify"], channel=ch)
        by_name = {c["name"]: c for c in get_map_context()["lanes"]}
        assert by_name["rec"]["delivery"]["state"] == "recording-only"
        assert by_name["gap"]["delivery"]["state"] == "no_channel"
        assert by_name["bad"]["delivery"]["state"] == "no_driver"

    def test_states_precedence_and_catch_all_label(self):
        self._lane("all", priority=1, match=[])
        self._lane("shadowed", priority=2, match=[{"field": "s", "op": "is", "value": "x"}])
        self._lane("off", priority=3, match=[], is_active=False)
        self._lane("broken", priority=4, match=[{"field": "s", "op": "zap", "value": 1}])
        by_name = {c["name"]: c for c in get_map_context()["lanes"]}
        assert by_name["all"]["conditions"] == []
        assert by_name["all"]["catch_all"] is True
        assert by_name["shadowed"]["state"] == "shadowed"
        assert by_name["shadowed"]["shadowed_by"] == "all"
        assert by_name["off"]["state"] == "inactive"
        assert by_name["broken"]["state"] == "never-matches"

    def test_empty_table(self):
        assert get_map_context()["lanes"] == []
```

**Step 2: run — failures.**

**Step 3: implement**

```python
from django.urls import reverse

from apps.orchestration.models import PipelineDefinition
from apps.orchestration.seeding import SEED_SHAPE_KEY


def _render_condition(c: dict) -> str:
    op, value = c.get("op", "is"), c.get("value")
    if isinstance(value, (list, tuple)):
        value = "[" + ", ".join(str(v) for v in value) + "]"
    return f"{c.get('field')} {op} {value}"


def _delivery(lane) -> dict:
    if "notify" not in lane.routable_stages():
        return {"state": "recording-only", "channel": None}
    gap = lane.delivery_gap()
    channel = lane.channel.name if lane.channel else None
    if gap is None:
        return {"state": "bound", "channel": lane.routed_channel().name}
    return {"state": gap, "channel": channel}


def get_map_context() -> dict:
    lanes = list(PipelineDefinition.objects.select_related("channel").order_by(...))
    # ^ COPY the exact ordering resolve_pipeline uses (apps/orchestration/orchestrator.py)
    #   — including all lanes, active or not; resolve_pipeline filters is_active,
    #   the map shows everything.
    shadows = _annotate_shadows(lanes)
    cards = []
    for lane in lanes:
        conds = lane.match if isinstance(lane.match, list) else []
        if not lane.is_active:
            state = "inactive"
        elif _never_matches(lane.match):
            state = "never-matches"
        elif lane.name in shadows:
            state = "shadowed"
        else:
            state = "ok"
        cards.append(
            {
                "name": lane.name,
                "state": state,
                "shadowed_by": shadows.get(lane.name),
                "catch_all": not conds,
                "conditions": [_render_condition(c) if isinstance(c, dict) else repr(c)
                               for c in conds],
                "stages": lane.routable_stages(),
                "delivery": _delivery(lane),
                "seeded": bool((lane.tags or {}).get(SEED_SHAPE_KEY))
                          if isinstance(lane.tags, dict) else False,
                "admin_url": reverse(
                    "admin:orchestration_pipelinedefinition_change", args=[lane.pk]
                ),
            }
        )
    return {"lanes": cards}
```

Adjust field names in tests to the real model (e.g. if `NotificationChannel` uses a
different driver/config field name, read `apps/notify/models.py` first and fix the
fixtures, not the assertions).

**Step 4: run — PASS. Step 5: commit** — `feat(config): get_map_context projects the routing table`

---

## Task 4: View, URL, template, dashboard link

**Files:**
- Modify: `config/admin.py` (add `get_urls` + view on `MonitoringAdminSite`)
- Create: `templates/admin/map.html`
- Modify: `templates/admin/dashboard.html` (link to the map near the readiness panel)
- Test: `config/_tests/test_netmap.py`

**Step 1: failing tests**

```python
@pytest.mark.django_db
class TestMapView:
    def test_requires_staff(self, client):
        assert client.get("/admin/map/").status_code == 302  # redirected to login

    def test_renders(self, admin_client):
        PipelineDefinition.objects.create(name="catch-all", priority=100, match=[])
        resp = admin_client.get("/admin/map/")
        assert resp.status_code == 200
        assert b"catch-all" in resp.content

    def test_empty_table_message(self, admin_client):
        resp = admin_client.get("/admin/map/")
        assert b"No routing configured" in resp.content
```

**Step 2: run — 404s.**

**Step 3: implement.** In `MonitoringAdminSite`:

```python
    def get_urls(self):
        from django.urls import path

        custom = [
            path("map/", self.admin_view(self.map_view), name="netmap"),
        ]
        return custom + super().get_urls()

    def map_view(self, request):
        from django.shortcuts import render

        from config.netmap import get_map_context

        context = {**self.each_context(request), "title": "Network map", **get_map_context()}
        return render(request, "admin/map.html", context)
```

(Match the file's existing import style — module-scope imports if that is what
`config/admin.py` does; the snippet above only marks *what* is needed.)

`templates/admin/map.html` — extends `admin/base_site.html`, `{% block content %}`:
one `<ol>` of lane cards in order. Inline `<style>` (copy the dashboard's approach):

- `.lane-card` bordered card; state classes `.ok` (default), `.shadowed` (grey,
  reduced opacity, banner "shadowed by <em>{{ shadowed_by }}</em>"),
  `.inactive` (dimmed + strikethrough name), `.never-matches` (red border,
  banner "never matches — malformed match").
- Conditions as `<code>` chips, or "matches everything" when `catch_all`.
- Stage chips `check → analyze → notify` from `stages`.
- Delivery line: bound → green "→ {{ channel }}"; recording-only → neutral
  "recording only"; `no_channel` → red "cannot deliver: no channel";
  `no_driver` → red "cannot deliver: driver not registered ({{ channel }})".
- Seed badge when `seeded`.
- Lane name links to `admin_url`.
- `{% if not lanes %}<p>No routing configured.</p>{% endif %}`
- An arrow/priority number gutter conveys "top wins"; caption: "Traffic takes the
  first matching lane."

Dashboard link: in `templates/admin/dashboard.html`, next to the readiness panel
heading, `<a href="{% url 'admin:netmap' %}">Network map</a>`.

**Step 4: run tests + `uv run python manage.py check` — PASS.**

**Step 5: commit** — `feat(admin): the network map page`

---

## Task 5: Verification, coverage, docs

**Steps:**

1. `uv run black . && uv run ruff check . --fix` — clean.
2. `uv run coverage run -m pytest && uv run coverage report` — 100% branch on
   `config/netmap.py` and changed lines of `config/admin.py`. Add missing-branch
   tests (likely: junk `tags`, non-dict cond rendering via `repr`, `_as_set` misses).
3. `uv run bandit -r apps/ config/ -c pyproject.toml` — clean.
4. Docs: add a short "Network map" paragraph to `docs/Architecture.md` (surface,
   what states mean) if the architecture doc describes the admin/dashboard;
   otherwise skip — no new env vars or commands to document. Do NOT touch
   existing files under `docs/plans/`.
5. Full suite: `uv run pytest` — green.
6. Commit — `docs: describe the network map surface` (only if docs changed).

**Acceptance criteria:**

- `/admin/map/` requires staff login and renders every lane in the exact order
  `resolve_pipeline` consults them.
- Shadowed lanes are grey and name their shadower; unprovable cases render normal.
- Inactive, never-matches, recording-only, `no_channel`, `no_driver`, bound all
  render distinctly; states match `delivery_gap()` semantics 1:1.
- Empty table shows "No routing configured".
- No new models/migrations/deps; all CI checks green; 100% branch on changed code.

{% endraw %}
