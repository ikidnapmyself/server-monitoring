---
title: "Hub-side policy overview page implementation plan"
parent: Plans
---

# Hub-side policy overview page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a read-only admin page at `/admin/policy/` listing every node's hub-side
re-evaluation policy override, one row per node and checker, with an Edit button that
lands on that checker's boxes on the node page.

**Architecture:** A new presentation module `apps/alerts/policy_overview.py` fans
`build_effective_policy(node)` out over every `Node` and flattens its three lists
(`sections`, `inactive`, `unread`) into one row per checker. A view on
`MonitoringAdminSite` renders it, following the existing `/admin/map/` page exactly.
No new storage, no new policy logic, no second writer for `Node.config`.

**Tech Stack:** Django 5.2, `django_object_actions`, pytest + pytest-django, uv.

Design: `docs/plans/2026-09-03-policy-overview-design.md`.

**Read first:**
- `apps/alerts/node_policy.py:454-650` — `PolicySection`, `UnreadKey`, `EffectivePolicy`,
  `build_effective_policy`. This plan consumes those and adds no policy rules.
- `apps/alerts/node_overview.py:1-50` — the module shape this one mirrors.
- `config/admin.py:44-52` — `get_urls` + `map_view`, the page pattern being copied.
- `config/netmap.py:1-10` — how a read-time projection module documents itself.

**Conventions that apply to every task:**
- Absolute imports only (`from apps.alerts...`).
- Line length 100 (Black + Ruff).
- 100% branch coverage on changed code.
- Every commit runs pre-commit (black, ruff, pytest, mypy). The pytest hook takes
  roughly two minutes, so give each `git commit` a 10 minute timeout.
- Branch is `feat/policy-overview-page`, already created. Never commit to main.

---

### Task 1: The row model and per-node flattening

**Files:**
- Create: `apps/alerts/policy_overview.py`
- Test: `apps/alerts/_tests/test_policy_overview.py`

**Step 1: Write the failing tests**

Create `apps/alerts/_tests/test_policy_overview.py`:

```python
"""Rows for the hub-side policy overview page.

Every case here is a shape ``Node.config`` can actually hold, because the ingest
path never validates it: a policy that scores, one with the right keys and an
unusable value, one whose keys nothing reads, and a checker that is both at once.
"""

from django.test import TestCase

from apps.alerts.models import Node
from apps.alerts.policy_overview import (
    IN_EFFECT,
    NOT_HONOURED,
    NOT_SCORING,
    NO_POLICY,
    rows_for_node,
)


class RowsForNodeTests(TestCase):
    def _node(self, config):
        return Node.objects.create(instance_id="node-a", hostname="a", config=config)

    def test_a_scoring_policy_is_one_row_in_effect(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "cpu")
        self.assertEqual(row.status, IN_EFFECT)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "")

    def test_a_half_filled_threshold_pair_is_not_scoring_with_the_forms_own_reason(self):
        node = self._node({"memory": {"warning_threshold": 90}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_SCORING)
        self.assertEqual(row.why, "Set a critical threshold too, or clear both.")

    def test_a_checker_no_scorer_reads_is_one_not_honoured_row(self):
        node = self._node({"disk_temp": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "disk_temp")
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(row.why, "Nothing reads disk_temp.")

    def test_a_scoring_checker_with_a_leftover_key_stays_one_row(self):
        # One decision an operator made, so one row. The ignored key rides along
        # in the reason rather than splitting into a second, contradictory row.
        node = self._node(
            {"cpu": {"warning_threshold": 90, "critical_threshold": 99, "spare": 1}}
        )
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "Nothing reads cpu → spare.")

    def test_an_editor_note_is_reported_on_a_row_that_scores(self):
        # 70000 is not a port the boxes accept, but _int_set coerces it, so it
        # really is in effect and cannot be retyped.
        node = self._node({"listening_ports": {"allowlist": [70000]}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, IN_EFFECT)
        self.assertIn("stricter than the scorers", row.why)

    def test_the_empty_section_marker_makes_no_row(self):
        # {"cpu": {}} is what opens a section in the form. It scores nothing and
        # holds no key for anything to ignore.
        self.assertEqual(rows_for_node(self._node({"cpu": {}})), [])

    def test_no_config_makes_no_rows(self):
        self.assertEqual(rows_for_node(self._node({})), [])

    def test_rows_are_sorted_by_checker(self):
        node = self._node(
            {
                "memory": {"warning_threshold": 1, "critical_threshold": 2},
                "cpu": {"warning_threshold": 1, "critical_threshold": 2},
            }
        )
        self.assertEqual([row.checker for row in rows_for_node(node)], ["cpu", "memory"])


class RowLinkTests(TestCase):
    def test_an_editable_row_links_to_that_checkers_own_box(self):
        node = Node.objects.create(
            instance_id="node-a",
            config={"cpu": {"warning_threshold": 90, "critical_threshold": 99}},
        )
        (row,) = rows_for_node(node)
        self.assertTrue(row.edit_url.startswith(row.node_url))
        self.assertTrue(row.edit_url.endswith("#id_policy__cpu__warning_threshold"))

    def test_a_row_with_no_boxes_links_to_the_page_with_no_fragment(self):
        node = Node.objects.create(
            instance_id="node-a", config={"disk_temp": {"warning_threshold": 60}}
        )
        (row,) = rows_for_node(node)
        self.assertEqual(row.edit_url, row.node_url)
```

**Step 2: Run them and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_policy_overview.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'apps.alerts.policy_overview'`.

**Step 3: Write the module**

Create `apps/alerts/policy_overview.py`:

```python
"""One table of every hub-side policy override on this hub.

``build_effective_policy`` already answers "what does this node's config
actually do?" for one node, in three lists. This module is that answer for the
fleet, flattened to one row per node and checker so a policy that scores nothing
is visible without opening eight change pages in turn.

It adds no rule of its own. Every status, value and sentence here comes from
``node_policy``, because a second opinion about what a stored threshold does is
a second thing to drift.

Design: docs/plans/2026-09-03-policy-overview-design.md
"""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_policy import build_effective_policy, field_name, spec_for

# The three ways a config entry ends up, in the words the page prints. They are
# the three lists ``EffectivePolicy`` returns, named once here so the template
# and the tests agree with the builder.
IN_EFFECT = "In effect"
NOT_SCORING = "Not scoring"
NOT_HONOURED = "Not honoured"

# What the Policy cell says for a checker with no value any scorer reads. A
# blank cell there reads as a rendering fault; this row is the whole point of
# the page, so it says so.
NO_POLICY = "—"


@dataclass(frozen=True)
class PolicyRow:
    """One checker's override on one node, ready to print."""

    checker: str
    policy: str
    status: str
    why: str
    node_url: str
    edit_url: str


def _edit_url(checker: str, node_url: str) -> str:
    """The node page, landing on this checker's own boxes where it has any.

    ``NodePolicyForm`` builds its fields through ``field_name`` and Django
    prefixes the rendered input with ``id_``, so the input is a stable anchor.
    The admin's fieldset template carries no id of its own, which is why the
    anchor is the first box rather than the section heading.

    A checker with no spec has no boxes on that page at all, so it gets the page
    and no fragment rather than a link to nothing.
    """
    spec = spec_for(checker)
    if not spec:
        return node_url
    return f"{node_url}#id_{field_name(checker, spec[0].name)}"


def _why(section, ignored: list[str]) -> str:
    """Why this row is not simply working, in the panel's own sentences.

    At most one explanation comes from the section itself: a section that scores
    nothing already carries the reason, and ``build_effective_policy`` only sets
    ``editor_note`` on a section that scores. Ignored keys are additional, since
    a checker can score and still hold a key nobody reads.
    """
    parts = []
    if section is not None and section.inactive_reason:
        parts.append(section.inactive_reason)
    elif section is not None and section.editor_note:
        parts.append(f"Scoring as stored, but {section.editor_note}")
    if ignored:
        parts.append(f"Nothing reads {', '.join(ignored)}.")
    return " ".join(parts)


def rows_for_node(node) -> list[PolicyRow]:
    """One row per checker this node has any config entry for.

    A checker can appear in two of ``EffectivePolicy``'s lists at once: a
    threshold pair that scores plus a leftover key nothing reads. That is one
    decision an operator made, so it stays one row, and the worse of the two
    statuses wins. Splitting it would put "In effect" and "Not honoured" beside
    each other for the same checker and leave the reader to work out which half
    of the entry each referred to.
    """
    policy = build_effective_policy(node)
    node_url = reverse("admin:alerts_node_change", args=[node.pk])
    ignored: dict[str, list[str]] = {}
    for entry in policy.unread:
        ignored.setdefault(entry.checker, []).append(entry.label)
    sections = {section.checker: (section, IN_EFFECT) for section in policy.sections}
    sections.update({section.checker: (section, NOT_SCORING) for section in policy.inactive})
    rows = []
    for checker in sorted(set(sections) | set(ignored)):
        section, status = sections.get(checker, (None, NOT_HONOURED))
        rows.append(
            PolicyRow(
                checker=checker,
                policy=(
                    ", ".join(f"{value.label} {value.value}" for value in section.values)
                    if section is not None
                    else NO_POLICY
                ),
                # An ignored key is not honoured whatever else the entry does.
                status=NOT_HONOURED if checker in ignored else status,
                why=_why(section, ignored.get(checker, [])),
                node_url=node_url,
                edit_url=_edit_url(checker, node_url),
            )
        )
    return rows
```

**Step 4: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_policy_overview.py -q
```

Expected: 10 passed.

**Step 5: Check coverage of the new module**

```bash
uv run coverage run -m pytest apps/alerts/_tests/test_policy_overview.py -q \
  && uv run coverage report --include="apps/alerts/policy_overview.py"
```

Expected: 100%. If a branch is missing, add the case rather than the pragma.

**Step 6: Commit**

```bash
git add apps/alerts/policy_overview.py apps/alerts/_tests/test_policy_overview.py
git commit -m "feat(alerts): flatten a node's effective policy into printable rows"
```

---

### Task 2: Grouping the fleet, problems first

**Files:**
- Modify: `apps/alerts/policy_overview.py`
- Test: `apps/alerts/_tests/test_policy_overview.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_policy_overview.py`:

```python
class BuildPolicyOverviewTests(TestCase):
    def _node(self, instance_id, config):
        return Node.objects.create(instance_id=instance_id, config=config)

    def test_a_node_with_a_problem_sorts_above_a_healthy_one(self):
        self._node("a-healthy", {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self._node("z-broken", {"cpu": {"warning_threshold": 1}})
        overview = build_policy_overview()
        self.assertEqual([g.instance_id for g in overview.groups], ["z-broken", "a-healthy"])

    def test_healthy_nodes_sort_among_themselves_by_instance_id(self):
        for name in ["b", "a"]:
            self._node(name, {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self.assertEqual(
            [g.instance_id for g in build_policy_overview().groups], ["a", "b"]
        )

    def test_a_node_with_no_policy_is_counted_not_listed(self):
        self._node("configured", {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self._node("quiet", {})
        self._node("marker-only", {"cpu": {}})
        overview = build_policy_overview()
        self.assertEqual([g.instance_id for g in overview.groups], ["configured"])
        self.assertEqual(overview.quiet_count, 2)

    def test_an_empty_hub_reads_as_nothing_configured(self):
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 0)
        self.assertFalse(overview.has_content)

    def test_has_content_is_true_once_one_node_is_configured(self):
        self._node("a", {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self.assertTrue(build_policy_overview().has_content)

    def test_a_group_carries_the_hostname_and_its_own_link(self):
        node = Node.objects.create(
            instance_id="a", hostname="a.local", config={"cpu": {"warning_threshold": 1}}
        )
        (group,) = build_policy_overview().groups
        self.assertEqual(group.hostname, "a.local")
        self.assertEqual(group.node_url, f"/admin/alerts/node/{node.pk}/change/")
        self.assertTrue(group.has_problem)
```

Extend the module import at the top of the file to
`from apps.alerts.policy_overview import (IN_EFFECT, NOT_HONOURED, NOT_SCORING, NO_POLICY, build_policy_overview, rows_for_node)`.

**Step 2: Run them and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_policy_overview.py -q
```

Expected: `ImportError: cannot import name 'build_policy_overview'`.

**Step 3: Extend the module**

Append to `apps/alerts/policy_overview.py`:

```python
@dataclass(frozen=True)
class NodeGroup:
    """One node's rows, under one heading."""

    instance_id: str
    hostname: str
    node_url: str
    rows: list[PolicyRow]
    has_problem: bool


@dataclass(frozen=True)
class PolicyOverview:
    """Every node holding policy, plus a count of the ones that hold none."""

    groups: list[NodeGroup]
    quiet_count: int

    @property
    def has_content(self) -> bool:
        """Whether any node on this hub overrides anything at all."""
        return bool(self.groups)


def build_policy_overview() -> PolicyOverview:
    """Every hub-side override on this hub, the broken ones first.

    A node with no config gets no rows: a table of dashes, one line per quiet
    machine, would bury the handful of rows the page exists to show. They are
    counted instead, so a reader can still tell the page is complete.

    Nodes holding anything that scores nothing sort to the top, because that is
    the failure this page answers: re-evaluation is fail-open, so an override
    doing nothing looks exactly like no override at all.
    """
    groups, quiet_count = [], 0
    for node in Node.objects.order_by("instance_id"):
        rows = rows_for_node(node)
        if not rows:
            quiet_count += 1
            continue
        groups.append(
            NodeGroup(
                instance_id=node.instance_id,
                hostname=node.hostname,
                node_url=rows[0].node_url,
                rows=rows,
                has_problem=any(row.status != IN_EFFECT for row in rows),
            )
        )
    groups.sort(key=lambda group: (not group.has_problem, group.instance_id))
    return PolicyOverview(groups=groups, quiet_count=quiet_count)
```

**Step 4: Run the tests and the coverage check**

```bash
uv run pytest apps/alerts/_tests/test_policy_overview.py -q
uv run coverage run -m pytest apps/alerts/_tests/test_policy_overview.py -q \
  && uv run coverage report --include="apps/alerts/policy_overview.py"
```

Expected: all pass, 100%.

**Step 5: Commit**

```bash
git add apps/alerts/policy_overview.py apps/alerts/_tests/test_policy_overview.py
git commit -m "feat(alerts): group policy rows by node, problems first"
```

---

### Task 3: The page itself

**Files:**
- Modify: `config/admin.py:44-52`
- Create: `templates/admin/policy_overview.html`
- Test: `config/_tests/test_policy_overview_view.py`

**Step 1: Write the failing tests**

Create `config/_tests/test_policy_overview_view.py`:

```python
"""The /admin/policy/ page.

Mirrors config/_tests/test_netmap.py: the projection itself is tested in
apps/alerts/_tests/test_policy_overview.py, so this covers reaching the page and
what the template does with what it is handed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.alerts.models import Node

pytestmark = pytest.mark.django_db


def test_anonymous_is_redirected_to_login(client):
    assert client.get(reverse("admin:policy-overview")).status_code == 302


def test_staff_without_view_node_is_refused(client):
    user = get_user_model().objects.create_user(
        username="plain", password="pw", is_staff=True
    )
    client.force_login(user)
    assert client.get(reverse("admin:policy-overview")).status_code == 403


def test_staff_with_view_node_gets_the_page(client):
    user = get_user_model().objects.create_user(
        username="viewer", password="pw", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_node"))
    client.force_login(user)
    response = client.get(reverse("admin:policy-overview"))
    assert response.status_code == 200
    assert "Hub-side policy" in response.content.decode()


def test_a_configured_node_renders_its_row_and_an_edit_link(admin_client):
    node = Node.objects.create(
        instance_id="fiyat-ekrani",
        config={"cpu": {"warning_threshold": 90, "critical_threshold": 99}},
    )
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "fiyat-ekrani" in body
    assert "Warning at 90, Critical at 99" in body
    assert f"/admin/alerts/node/{node.pk}/change/#id_policy__cpu__warning_threshold" in body


def test_a_broken_policy_shows_its_reason(admin_client):
    Node.objects.create(instance_id="a", config={"cpu": {"warning_threshold": 90}})
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "Not scoring" in body
    assert "Set a critical threshold too, or clear both." in body


def test_nodes_with_no_policy_are_counted(admin_client):
    Node.objects.create(instance_id="a", config={"cpu": {"warning_threshold": 1}})
    Node.objects.create(instance_id="b", config={})
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "1 other node has no hub-side policy" in body


def test_an_unconfigured_hub_says_so(admin_client):
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "No node on this hub overrides anything" in body


def test_a_hostname_is_escaped(admin_client):
    # instance_id and hostname both arrive over a webhook.
    Node.objects.create(
        instance_id="<script>x</script>",
        hostname="<b>y</b>",
        config={"cpu": {"warning_threshold": 1}},
    )
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
```

**Step 2: Run them and watch them fail**

```bash
uv run pytest config/_tests/test_policy_overview_view.py -q
```

Expected: `NoReverseMatch: 'policy-overview' is not a valid view function or pattern name`.

**Step 3: Add the view**

In `config/admin.py`, add to the imports:

```python
from django.core.exceptions import PermissionDenied

from apps.alerts.policy_overview import build_policy_overview
```

Extend `get_urls` and add the view beside `map_view`:

```python
    def get_urls(self):
        custom = [
            path("map/", self.admin_view(self.map_view), name="netmap"),
            path("policy/", self.admin_view(self.policy_view), name="policy-overview"),
        ]
        return custom + super().get_urls()

    def policy_view(self, request):
        """Every node's hub-side policy in one table.

        ``admin_view`` only asks for staff. This page prints the same facts the
        Node change page shows a viewer, so it asks for the same permission
        rather than being readable by any staff account that cannot open a node.
        """
        if not request.user.has_perm("alerts.view_node"):
            raise PermissionDenied
        context = {
            **self.each_context(request),
            "overview": build_policy_overview(),
            "title": "Hub-side policy",
        }
        return render(request, "admin/policy_overview.html", context)
```

**Step 4: Add the template**

Create `templates/admin/policy_overview.html`:

```html
{% extends "admin/base_site.html" %}
{% comment %}
Every hub-side policy override on this hub, one row per node and checker.
Context comes from MonitoringAdminSite.policy_view → build_policy_overview().

Everything here is autoescaped and must stay that way: instance_id and hostname
both arrive over a webhook, and a checker name comes from the same payload.

The Edit link carries a fragment naming a form input on the node page, so it
lands on that checker's own boxes. A row with no boxes links to the page plain.
{% endcomment %}

{% block content %}
<div id="policy-overview">
  <p style="color:var(--body-quiet-color, #666); font-size:13px; margin:4px 0 16px;">
    Re-evaluation is fail-open, so an override the scorers cannot use looks exactly
    like no override. Anything not marked "In effect" is changing no severity.
  </p>

  {% if overview.has_content %}
  {% for group in overview.groups %}
  <div class="module" style="margin-bottom:16px; padding:12px 16px;">
    <h2 style="margin-top:0;">
      <a href="{{ group.node_url }}">{{ group.instance_id }}</a>
      {% if group.hostname %}
      <span style="font-weight:normal; font-size:13px;">{{ group.hostname }}</span>
      {% endif %}
    </h2>
    <div style="overflow-x:auto;">
      <table style="width:100%; border-collapse:collapse;">
        <thead>
          <tr>
            <th style="text-align:left;">Checker</th>
            <th style="text-align:left;">Policy</th>
            <th style="text-align:left;">Status</th>
            <th style="text-align:left;">Why</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for row in group.rows %}
          <tr>
            <td>{{ row.checker }}</td>
            <td>{{ row.policy }}</td>
            <td>
              {% if row.status == "In effect" %}
              <span style="background-color:#28a745; color:#fff; padding:2px 8px;
                           border-radius:10px; font-size:12px;">{{ row.status }}</span>
              {% else %}
              <span style="background-color:#b26a00; color:#fff; padding:2px 8px;
                           border-radius:10px; font-size:12px;">{{ row.status }}</span>
              {% endif %}
            </td>
            <td>{{ row.why }}</td>
            <td style="text-align:right;"><a href="{{ row.edit_url }}">Edit</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endfor %}
  {% else %}
  <p>No node on this hub overrides anything, so every alert scores as it arrives.</p>
  {% endif %}

  {% if overview.quiet_count %}
  <p style="color:var(--body-quiet-color, #666);">
    {{ overview.quiet_count }} other node{{ overview.quiet_count|pluralize }}
    {{ overview.quiet_count|pluralize:"has,have" }} no hub-side policy.
  </p>
  {% endif %}
</div>
{% endblock %}
```

**Step 5: Run the tests**

```bash
uv run pytest config/_tests/test_policy_overview_view.py -q
```

Expected: 8 passed.

**Step 6: Commit**

```bash
git add config/admin.py templates/admin/policy_overview.html \
  config/_tests/test_policy_overview_view.py
git commit -m "feat(admin): add the hub-side policy overview page"
```

---

### Task 4: Two ways to reach it

**Files:**
- Create: `templates/admin/alerts/node/change_list.html`
- Modify: `apps/alerts/admin.py` (NodeAdmin: add `change_list_template`)
- Modify: `templates/admin/dashboard.html:285`
- Test: `apps/alerts/_tests/test_node_admin.py`, `config/_tests/test_dashboard_render.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_node_admin.py`:

```python
class NodeChangelistPolicyLinkTests(TestCase):
    def test_the_changelist_offers_the_policy_overview(self):
        user = get_user_model().objects.create_superuser(
            username="su", email="su@example.com", password="pw"
        )
        self.client.force_login(user)
        body = self.client.get(reverse("admin:alerts_node_changelist")).content.decode()
        self.assertIn(reverse("admin:policy-overview"), body)

    def test_the_object_action_buttons_survive_the_override(self):
        # The changelist template must extend the django_object_actions one, or
        # its object-tools block silently replaces the action buttons.
        model_admin = self._admin()
        self.assertEqual(
            model_admin.change_list_template, "admin/alerts/node/change_list.html"
        )
```

Add to `config/_tests/test_dashboard_render.py`, beside the existing netmap assertion:

```python
    assert reverse("admin:policy-overview") in body  # readiness heading links to policy too
```

**Step 2: Run them and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_node_admin.py -k Policy config/_tests/test_dashboard_render.py -q
```

Expected: the link assertions fail.

**Step 3: Add the template and wire it**

Create `templates/admin/alerts/node/change_list.html`:

```html
{% extends "django_object_actions/change_list.html" %}
{% comment %}
Adds "Policy overview" to the changelist object tools.

Extends the django_object_actions template rather than admin/change_list.html
for the same reason the change_form template does: that template fills
object-tools-items with the changelist_actions buttons, and extending the plain
admin one silently drops them. block.super keeps them beside this link.
{% endcomment %}

{% block object-tools-items %}
  <li><a href="{% url 'admin:policy-overview' %}">Policy overview</a></li>
  {{ block.super }}
{% endblock %}
```

In `apps/alerts/admin.py`, on `NodeAdmin`, beside the existing `change_form_template`:

```python
    change_list_template = "admin/alerts/node/change_list.html"
```

In `templates/admin/dashboard.html:285`, add the second link beside the network map one,
using the same `netmap-link` class so it picks up the existing heading-link styling:

```html
  <h2>Readiness
    <a class="netmap-link" href="{% url 'admin:netmap' %}">Network map</a>
    <a class="netmap-link" href="{% url 'admin:policy-overview' %}">Hub-side policy</a>
  </h2>
```

**Step 4: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_node_admin.py config/_tests/test_dashboard_render.py -q
```

Expected: all pass.

**Step 5: Commit**

```bash
git add templates/admin/alerts/node/change_list.html templates/admin/dashboard.html \
  apps/alerts/admin.py apps/alerts/_tests/test_node_admin.py \
  config/_tests/test_dashboard_render.py
git commit -m "feat(admin): reach the policy overview from the dashboard and node list"
```

---

### Task 5: Full verification

**Step 1: The whole suite**

```bash
uv run pytest -q
```

Expected: all pass, no new warnings.

**Step 2: Coverage on the changed modules**

```bash
uv run coverage run -m pytest -q
uv run coverage report --include="apps/alerts/policy_overview.py,config/admin.py"
```

Expected: 100% on `apps/alerts/policy_overview.py`, and no drop on `config/admin.py`.

**Step 3: Format, lint, types, security**

```bash
uv run black . --check
uv run ruff check .
uv run mypy .
uv run bandit -r apps/ config/ -c pyproject.toml
```

**Step 4: Look at the page**

```bash
uv run python manage.py runserver
```

Open `/admin/alerts/node/` and use the Policy overview button. Confirm: a broken
node sorts first, an Edit link lands on the right box with the browser scrolled to
it, and the dashboard readiness heading carries the second link.

**Step 5: Push and open the PR**

```bash
git push -u origin feat/policy-overview-page
gh pr create --title "Hub-side policy overview page" --body "..."
```

Never push to main. The PR body should link both plan documents.

---

## Notes for the implementer

- **Do not add editing here.** `NodePolicyForm` is deliberately the only writer for
  `Node.config`; the Node admin drops the raw JSON widget for exactly that reason.
  If a row seems to want an inline threshold box, that is the wrong instinct.
- **Do not restate a policy rule.** If the page needs to know whether a stored value
  scores, ask `node_policy`. Every sentence in a Why cell already exists there.
- **`_tests` mirrors source.** `apps/alerts/policy_overview.py` →
  `apps/alerts/_tests/test_policy_overview.py`; the view lives on the admin site, so
  its test lives under `config/_tests/`.
