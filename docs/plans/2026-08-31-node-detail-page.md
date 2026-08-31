---
title: "Node detail page overview — implementation"
parent: Plans
---

{% raw %}

# Node Detail Page Overview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `/admin/alerts/node/<id>/change/` from a registry form into an operator overview, and fix `latest preflight` never resolving for the hub's own node.

**Architecture:** A new pure-Python module `apps/alerts/node_overview.py` builds every panel as plain dataclasses. `NodeAdmin.render_change_form` puts one `node_overview` object in the context; `templates/admin/alerts/node/change_form.html` renders it above the normal fieldsets. Panel logic never touches the admin, so every test is a direct function call. Separately, `manage.py preflight` starts writing the same instance id the `Node` registry uses, and a data migration repairs the rows already written with a blank one.

**Tech Stack:** Django 5.2, Django admin, pytest + pytest-django, `uv` for everything.

**Design doc:** `docs/plans/2026-08-31-node-detail-page-design.md`. Read it first.

**Before you start:**

```bash
uv sync --extra dev
uv run pytest apps/alerts apps/checkers -q     # must be green before you change anything
```

Rules from `AGENTS.md` that this plan assumes and does not repeat:
absolute imports only, 100 lines max width, 100% branch coverage on changed code,
`format_html` never `mark_safe`, commit after every task.

---

## Background you need

**Two spellings of "this machine".** `apps/alerts/identity.py` defines
`local_instance_id()`, which returns `settings.INSTANCE_ID` or falls back to
`socket.gethostname()`. Every `Node` row for this machine is keyed by that
function. `manage.py preflight` instead writes `settings.INSTANCE_ID` raw, which
is `""` on any hub that never set the env var. That mismatch is the bug.

**Where metric history lives.** `CheckRun` rows are written in one place,
`apps/checkers/checkers/base.py:126`, by the machine that ran the checker. A hub
has `CheckRun` rows for itself and none for its peers. Peers arrive as `Alert`
rows deduped on `(fingerprint, source)` and updated in place: current state, never
a series. Charts are therefore local-node-only by design, and peers get an
explicit sentence instead of an empty chart. Do not try to fix this in this plan.

**Primary metric per checker.** There is an existing comment block in
`apps/alerts/admin.py` listing which metric each numeric checker reads. This plan
promotes it to a real dict.

---

### Task 1: preflight writes the registry's instance id

**Files:**
- Modify: `apps/checkers/management/commands/preflight.py`
- Test: `apps/checkers/_tests/preflight/test_command.py`

**Step 1: Write the failing test**

Append to `PreflightCommandTests` in `apps/checkers/_tests/preflight/test_command.py`:

```python
    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(INSTANCE_ID="")
    def test_persisted_run_uses_the_registry_instance_id(self, mock_log, mock_read):
        # A hub never sets INSTANCE_ID, but its Node row is keyed by
        # local_instance_id(), which falls back to the hostname. Writing the raw
        # setting here left the run orphaned from its own node page.
        mock_read.return_value = None
        self._call()
        run = PreflightRun.objects.latest("created_at")
        self.assertEqual(run.instance_id, local_instance_id())
        self.assertNotEqual(run.instance_id, "")
```

Add the imports at the top of that file:

```python
from apps.alerts.identity import local_instance_id
from apps.checkers.models import PreflightRun
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/checkers/_tests/preflight/test_command.py::PreflightCommandTests::test_persisted_run_uses_the_registry_instance_id -v
```

Expected: FAIL, `'' != '<your hostname>'`.

**Step 3: Write minimal implementation**

In `apps/checkers/management/commands/preflight.py`, add the import:

```python
from apps.alerts.identity import local_instance_id
```

and in `_persist`, change the one line:

```python
        run = PreflightRun.objects.create(
            instance_id=local_instance_id(),
```

If `from django.conf import settings` becomes unused, delete it. Run
`uv run ruff check apps/checkers` to find out rather than guessing.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/checkers/_tests/preflight -v
```

Expected: PASS, no other test in that file regressed.

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/preflight.py apps/checkers/_tests/preflight/test_command.py
git commit -m "fix(checkers): preflight records the instance id the node registry uses"
```

---

### Task 2: backfill the preflight runs already written blank

**Files:**
- Create: `apps/checkers/migrations/0003_backfill_preflight_instance_id.py`
- Test: `apps/checkers/_tests/test_preflight_backfill_migration.py`

**Step 1: Write the failing test**

Create `apps/checkers/_tests/test_preflight_backfill_migration.py`:

```python
"""The 0003 data migration repairs preflight rows written with a blank id.

Exercised as a plain function rather than through the migration executor: the
migration body is the unit under test, and calling it directly keeps the test
fast and readable.
"""

from django.apps import apps as django_apps
from django.test import TestCase

from apps.alerts.identity import local_instance_id
from apps.checkers.migrations import (
    _0003_backfill_preflight_instance_id as backfill_module,
)
from apps.checkers.models import PreflightRun


class PreflightBackfillTests(TestCase):
    def test_blank_rows_get_this_machines_id(self):
        blank = PreflightRun.objects.create(instance_id="", overall_status="ok")
        backfill_module.backfill(django_apps, None)
        blank.refresh_from_db()
        self.assertEqual(blank.instance_id, local_instance_id())

    def test_rows_that_already_name_a_machine_are_left_alone(self):
        named = PreflightRun.objects.create(instance_id="web-03", overall_status="ok")
        backfill_module.backfill(django_apps, None)
        named.refresh_from_db()
        self.assertEqual(named.instance_id, "web-03")
```

Note the import name: a module starting with a digit cannot be imported, so the
migration file needs an importable alias. Do **not** rename the migration. Add
this to `apps/checkers/migrations/__init__.py` instead:

```python
"""Migrations for the checkers app.

Migration modules start with a digit and so cannot be imported by name. The
0003 data migration has test coverage on its callable, so it gets an importable
alias here.
"""

from importlib import import_module

_0003_backfill_preflight_instance_id = import_module(
    "apps.checkers.migrations.0003_backfill_preflight_instance_id"
)
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/checkers/_tests/test_preflight_backfill_migration.py -v
```

Expected: FAIL with `ModuleNotFoundError` / `ImportError` — the migration does not exist yet.

**Step 3: Write minimal implementation**

Create `apps/checkers/migrations/0003_backfill_preflight_instance_id.py`:

```python
"""Stamp this machine's id onto preflight runs written before the writer fix.

``manage.py preflight`` used to persist ``settings.INSTANCE_ID`` raw, which is
empty on any hub that never set the env var. Those rows never matched the hub's
own ``Node``, which is keyed by ``local_instance_id()``, so the node page said
"No preflight recorded" while the changelist listed the runs. Preflight is
node-local and never pushed, so every blank row on this database was written
here: stamping them with this machine's id is correct, not a guess.
"""

from django.db import migrations

from apps.alerts.identity import local_instance_id


def backfill(apps, schema_editor):
    PreflightRun = apps.get_model("checkers", "PreflightRun")
    PreflightRun.objects.filter(instance_id="").update(instance_id=local_instance_id())


def unbackfill(apps, schema_editor):
    """Not reversible in a meaningful way; a no-op keeps ``migrate`` backwards working."""


class Migration(migrations.Migration):
    dependencies = [
        ("checkers", "0002_preflightrun_preflightcheck"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/checkers/_tests/test_preflight_backfill_migration.py -v
uv run python manage.py migrate --plan | tail -5
```

Expected: PASS, and the plan lists `checkers.0003_backfill_preflight_instance_id`.

**Step 5: Commit**

```bash
git add apps/checkers/migrations/ apps/checkers/_tests/test_preflight_backfill_migration.py
git commit -m "fix(checkers): backfill preflight runs written with a blank instance id"
```

---

### Task 3: the overview module skeleton and the identity header

**Files:**
- Create: `apps/alerts/node_overview.py`
- Test: `apps/alerts/_tests/test_node_overview.py`

**Step 1: Write the failing test**

Create `apps/alerts/_tests/test_node_overview.py`:

```python
from django.test import TestCase
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Node
from apps.alerts.node_overview import build_identity


class IdentityHeaderTests(TestCase):
    def test_the_local_node_is_named_as_this_hub(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        identity = build_identity(node)
        self.assertTrue(identity.is_local)
        self.assertEqual(identity.role_label, "This hub")

    def test_any_other_node_is_a_peer(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        identity = build_identity(node)
        self.assertFalse(identity.is_local)
        self.assertEqual(identity.role_label, "Peer")

    def test_a_node_seen_just_now_reads_green(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_identity(node).freshness_status, "ok")

    def test_a_node_quiet_past_the_dashboard_window_reads_amber(self):
        # Same threshold the dashboard nodes card uses, so the two never disagree.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Node.objects.filter(pk=node.pk).update(
            last_seen=timezone.now() - timezone.timedelta(minutes=NODE_RECENT_MINUTES + 1)
        )
        node.refresh_from_db()
        self.assertEqual(build_identity(node).freshness_status, "warn")

    def test_the_freshness_label_carries_an_age(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertIn("ago", build_identity(node).freshness_label)
```

Add to the imports: `from config.dashboard import NODE_RECENT_MINUTES`.

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

Expected: FAIL, `ModuleNotFoundError: apps.alerts.node_overview`.

**Step 3: Write minimal implementation**

Create `apps/alerts/node_overview.py`:

```python
"""Panels for the Node admin detail page.

Every panel is a plain function returning a plain dataclass, so the whole page
is testable without driving the admin. ``NodeAdmin.render_change_form`` calls
``build_node_overview`` and the change_form template renders the result.

Nothing here writes. Nothing here knows about requests.
"""

from dataclasses import dataclass

from django.utils import timezone
from django.utils.timesince import timesince

from apps.alerts.identity import local_instance_id
from config.dashboard import NODE_RECENT_MINUTES


@dataclass(frozen=True)
class Identity:
    is_local: bool
    role_label: str
    freshness_status: str  # "ok" | "warn"
    freshness_label: str


def build_identity(node) -> Identity:
    """Role and freshness for the header.

    Freshness reuses the dashboard's own window rather than restating a number,
    so a node that reads amber on the dashboard reads amber here.
    """
    is_local = node.instance_id == local_instance_id()
    now = timezone.now()
    cutoff = now - timezone.timedelta(minutes=NODE_RECENT_MINUTES)
    status = "ok" if node.last_seen >= cutoff else "warn"
    return Identity(
        is_local=is_local,
        role_label="This hub" if is_local else "Peer",
        freshness_status=status,
        freshness_label=f"{timesince(node.last_seen, now)} ago",
    )
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

Expected: PASS, 5 tests.

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/_tests/test_node_overview.py
git commit -m "feat(admin): node overview identity and freshness header"
```

---

### Task 4: share the severity chips between changelist and detail page

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Modify: `apps/alerts/admin.py:731-760` (the `incidents` display method)
- Test: `apps/alerts/_tests/test_node_overview.py`

The chips exist today only inside `NodeAdmin.incidents`, built from the
annotations `NodeAdmin.get_queryset` adds. The detail page needs the same chips,
and duplicating the markup would let the two drift.

**Step 1: Write the failing test**

Append to `apps/alerts/_tests/test_node_overview.py`:

```python
class SeverityChipTests(TestCase):
    def _incident(self, node, severity, status=IncidentStatus.OPEN):
        incident = Incident.objects.create(
            title="disk full", severity=severity, status=status
        )
        Alert.objects.create(
            fingerprint=f"f-{incident.pk}",
            source="cluster",
            name="disk",
            severity=severity,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        return incident

    def test_counts_unresolved_incidents_once_per_incident(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = self._incident(node, AlertSeverity.CRITICAL)
        # A second alert on the SAME incident must not double the count.
        Alert.objects.create(
            fingerprint="f-second",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.CRITICAL,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        counts = unresolved_counts(node)
        self.assertEqual(counts[AlertSeverity.CRITICAL], 1)

    def test_resolved_incidents_are_not_counted(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.CRITICAL, status=IncidentStatus.RESOLVED)
        self.assertEqual(unresolved_counts(node)[AlertSeverity.CRITICAL], 0)

    def test_a_quiet_node_renders_a_dash_not_a_zero(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(render_severity_chips(node), "—")

    def test_each_chip_links_to_that_severity_on_the_changelist(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.WARNING)
        html = render_severity_chips(node)
        self.assertIn(f"alerts__node__id__exact={node.pk}", html)
        self.assertIn("severity__exact=warning", html)
        self.assertIn("1 WARNING", html)

    def test_annotated_counts_are_reused_when_present(self):
        # The changelist annotates; the helper must not re-query in that case.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.CRITICAL)
        annotated = NodeAdmin(Node, admin.site).get_queryset(None).get(pk=node.pk)
        with self.assertNumQueries(0):
            counts = unresolved_counts(annotated)
        self.assertEqual(counts[AlertSeverity.CRITICAL], 1)
```

Imports this test file needs:

```python
from django.contrib import admin

from apps.alerts.admin import NodeAdmin
from apps.alerts.models import Alert, AlertSeverity, Incident, IncidentStatus
from apps.alerts.node_overview import render_severity_chips, unresolved_counts
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py::SeverityChipTests -v
```

Expected: FAIL, `ImportError: cannot import name 'render_severity_chips'`.

**Step 3: Write minimal implementation**

Move the constants and the chip markup into `apps/alerts/node_overview.py`:

```python
SEVERITY_COLORS = {
    AlertSeverity.CRITICAL: "#dc3545",
    AlertSeverity.WARNING: "#ffc107",
    AlertSeverity.INFO: "#17a2b8",
}

# Worst first: the order both the changelist column and the detail header read in.
SEVERITIES_WORST_FIRST = [AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO]

# An acknowledged incident is a live problem someone has picked up. Counting it
# as handled would make a node look healthy the moment an operator touched it.
UNRESOLVED_INCIDENT_STATUSES = [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]


def unresolved_counts(node) -> dict[str, int]:
    """Unresolved incidents per severity for one node.

    Prefers the annotations ``NodeAdmin.get_queryset`` adds, so rendering the
    whole changelist stays one query. Falls back to a per-node aggregate for a
    node that arrived unannotated. ``distinct=True`` throughout: an incident this
    node raised six alerts on is one incident, not six.
    """
    if hasattr(node, "unresolved_total"):
        return {s: getattr(node, f"unresolved_{s}", 0) for s in SEVERITIES_WORST_FIRST}
    rows = (
        Incident.objects.filter(
            alerts__node=node, status__in=UNRESOLVED_INCIDENT_STATUSES
        )
        .values("severity")
        .annotate(count=Count("pk", distinct=True))
    )
    counted = {row["severity"]: row["count"] for row in rows}
    return {s: counted.get(s, 0) for s in SEVERITIES_WORST_FIRST}


def render_severity_chips(node):
    """Linked severity chips, worst first. A quiet node reads as a dash.

    Each chip links to the incident changelist already narrowed to this node and
    severity, reusing the ``alerts__node`` filter rather than a parallel view.
    """
    counts = unresolved_counts(node)
    parts = []
    for severity in SEVERITIES_WORST_FIRST:
        count = counts.get(severity, 0)
        if not count:
            continue
        url = "{}?alerts__node__id__exact={}&status__in={}&severity__exact={}".format(
            reverse("admin:alerts_incident_changelist"),
            node.pk,
            ",".join(UNRESOLVED_INCIDENT_STATUSES),
            severity,
        )
        parts.append(
            format_html(
                '<a href="{}" style="background-color: {}; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; text-decoration: none;">{} {}</a>',
                url,
                SEVERITY_COLORS.get(severity, "#6c757d"),
                count,
                severity.upper(),
            )
        )
    if not parts:
        return "—"
    return format_html_join(" ", "{}", ((part,) for part in parts))
```

Then in `apps/alerts/admin.py`, collapse the display method to a delegate:

```python
    @admin.display(description="Incidents", ordering="unresolved_total")
    def incidents(self, obj):
        """Unresolved incident counts for this node, worst severity first."""
        return render_severity_chips(obj)
```

and re-export the constants from `node_overview` rather than defining them twice:

```python
from apps.alerts.node_overview import (
    SEVERITIES_WORST_FIRST,
    SEVERITY_COLORS,
    UNRESOLVED_INCIDENT_STATUSES,
    render_severity_chips,
)
```

Delete the now-duplicate definitions from `admin.py`. Other admin classes in that
file use `SEVERITY_COLORS`; the re-export keeps them working. Confirm with
`uv run ruff check apps/alerts`.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts -q
```

Expected: PASS, including the pre-existing `test_node_admin.py` chip tests, unchanged.

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/admin.py apps/alerts/_tests/test_node_overview.py
git commit -m "refactor(admin): share node severity chips between changelist and detail"
```

---

### Task 5: the per-checker current state panel

This is the panel that works for every node, and the reason the page is worth
opening. Local node reads `CheckRun`; a peer reads its `Alert` rows. Both produce
the same row type so the template has one loop.

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Test: `apps/alerts/_tests/test_node_overview.py`

**Step 1: Write the failing test**

```python
class CheckerStateTests(TestCase):
    def test_local_node_reads_its_own_check_runs_newest_first_per_checker(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        CheckRun.objects.create(
            checker_name="disk", hostname="hub", status="ok",
            metrics={"worst_percent": 40.0},
            executed_at=timezone.now() - timezone.timedelta(minutes=10),
        )
        CheckRun.objects.create(
            checker_name="disk", hostname="hub", status="critical",
            metrics={"worst_percent": 91.0}, executed_at=timezone.now(),
        )
        rows = build_checker_rows(node)
        self.assertEqual([r.checker for r in rows], ["disk"])
        self.assertEqual(rows[0].status, "critical")
        self.assertIn("91", rows[0].value)

    def test_a_peer_reads_its_alert_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:cpu", source="cluster", name="cpu",
            severity=AlertSeverity.WARNING, started_at=timezone.now(), node=node,
            labels={"checker": "cpu"}, annotations={"cpu_percent": "93.5"},
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "cpu")
        self.assertIn("93.5", rows[0].value)
        self.assertEqual(rows[0].status, AlertSeverity.WARNING)

    def test_a_peer_alert_with_no_checker_label_is_skipped(self):
        # Webhook alerts are not checker results and have no place in this table.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="grafana-1", source="grafana", name="latency",
            severity=AlertSeverity.WARNING, started_at=timezone.now(), node=node,
        )
        self.assertEqual(build_checker_rows(node), [])

    def test_a_checker_with_no_known_primary_metric_still_renders(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:raid", source="cluster", name="raid",
            severity=AlertSeverity.INFO, started_at=timezone.now(), node=node,
            labels={"checker": "raid"}, annotations={},
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "raid")
        self.assertEqual(rows[0].value, "—")

    def test_a_node_that_reported_nothing_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_checker_rows(node), [])

    def test_rows_are_sorted_by_checker_name(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for name in ("memory", "cpu", "disk"):
            Alert.objects.create(
                fingerprint=f"check:web-03:{name}", source="cluster", name=name,
                severity=AlertSeverity.INFO, started_at=timezone.now(), node=node,
                labels={"checker": name}, annotations={},
            )
        self.assertEqual([r.checker for r in build_checker_rows(node)], ["cpu", "disk", "memory"])
```

Add imports: `from apps.alerts.node_overview import build_checker_rows` and
`from apps.checkers.models import CheckRun`.

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py::CheckerStateTests -v
```

Expected: FAIL, `cannot import name 'build_checker_rows'`.

**Step 3: Write minimal implementation**

Append to `apps/alerts/node_overview.py`:

```python
# The metric each numeric checker is judged on. Promoted from a comment in
# admin.py so the state table and the charts read the same field.
CHECKER_PRIMARY_METRIC = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "disk": "worst_percent",
    "disk_inodes": "worst_percent",
    "disk_temp": "hottest_c",
    "cpu_temp": "hottest_c",
    "io_strain": "busiest_util_percent",
}


@dataclass(frozen=True)
class CheckerRow:
    checker: str
    status: str
    value: str
    observed_at: object
    url: str


def _format_metric(raw) -> str:
    """One display string for a metric value, whatever type it arrived as.

    Peer values come off ``Alert.annotations`` as strings; local values come off
    ``CheckRun.metrics`` as numbers. Anything missing or unparseable reads as a
    dash, never as a stack trace on an operator's page.
    """
    if raw is None or raw == "":
        return "—"
    try:
        return f"{float(raw):.1f}"
    except (TypeError, ValueError):
        return str(raw)


def _local_checker_rows(node) -> list[CheckerRow]:
    """Newest CheckRun per checker for the machine this hub runs on."""
    newest: dict[str, object] = {}
    runs = CheckRun.objects.filter(hostname=node.hostname).order_by("-executed_at")
    for run in runs.iterator():
        newest.setdefault(run.checker_name, run)
    rows = []
    for name, run in newest.items():
        metric = CHECKER_PRIMARY_METRIC.get(name)
        raw = (run.metrics or {}).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=name,
                status=run.status,
                value=_format_metric(raw),
                observed_at=run.executed_at,
                url=reverse("admin:checkers_checkrun_change", args=[run.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def _peer_checker_rows(node) -> list[CheckerRow]:
    """A peer has no CheckRun here — its current state is its Alert rows.

    One Alert per checker per node by fingerprint, updated in place on every
    push, so the newest write is the whole history this hub holds.
    """
    rows = []
    for alert in node.alerts.order_by("-updated_at"):
        checker = (alert.labels or {}).get("checker")
        if not checker:
            continue  # a webhook alert is not a checker result
        if any(r.checker == checker for r in rows):
            continue
        metric = CHECKER_PRIMARY_METRIC.get(checker)
        raw = (alert.annotations or {}).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=checker,
                status=alert.severity,
                value=_format_metric(raw),
                observed_at=alert.updated_at,
                url=reverse("admin:alerts_alert_change", args=[alert.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def build_checker_rows(node) -> list[CheckerRow]:
    """Current per-checker state, from whichever source this node has."""
    if build_identity(node).is_local:
        return _local_checker_rows(node)
    return _peer_checker_rows(node)
```

Add the imports this needs at the top of the module: `Count`, `reverse`,
`format_html`, `format_html_join`, `Alert`/`AlertSeverity`/`Incident`/
`IncidentStatus` from `apps.alerts.models`, and `CheckRun` from
`apps.checkers.models`. Watch for a circular import: `apps.checkers.models` does
not import `apps.alerts.node_overview`, so a module-level import is fine here.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/_tests/test_node_overview.py
git commit -m "feat(admin): per-checker current state for local and peer nodes"
```

---

### Task 6: the recent incidents panel

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Test: `apps/alerts/_tests/test_node_overview.py`

**Step 1: Write the failing test**

```python
class RecentIncidentTests(TestCase):
    def _incident(self, node, title, severity=AlertSeverity.WARNING):
        incident = Incident.objects.create(title=title, severity=severity)
        Alert.objects.create(
            fingerprint=f"f-{title}", source="cluster", name=title,
            severity=severity, started_at=timezone.now(), node=node, incident=incident,
        )
        return incident

    def test_lists_the_nodes_incidents_newest_first(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, "older")
        self._incident(node, "newer")
        rows = build_incident_rows(node)
        self.assertEqual([r.title for r in rows], ["newer", "older"])

    def test_counts_an_incident_once_however_many_alerts_reached_it(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = self._incident(node, "disk full")
        Alert.objects.create(
            fingerprint="f-second", source="cluster", name="disk",
            severity=AlertSeverity.WARNING, started_at=timezone.now(),
            node=node, incident=incident,
        )
        self.assertEqual(len(build_incident_rows(node)), 1)

    def test_caps_at_ten(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for i in range(12):
            self._incident(node, f"i{i}")
        self.assertEqual(len(build_incident_rows(node)), 10)

    def test_another_nodes_incidents_are_not_listed(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        other = Node.objects.create(instance_id="web-04", hostname="web-04")
        self._incident(other, "theirs")
        self.assertEqual(build_incident_rows(node), [])

    def test_a_node_with_no_incidents_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_incident_rows(node), [])
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py::RecentIncidentTests -v
```

Expected: FAIL, `cannot import name 'build_incident_rows'`.

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class IncidentRow:
    title: str
    severity: str
    status: str
    color: str
    created_at: object
    url: str


def build_incident_rows(node, limit: int = 10) -> list[IncidentRow]:
    """The node's newest incidents. The chips give counts; this gives names.

    ``distinct()`` because an incident this node raised six alerts on must appear
    once, not six times.
    """
    incidents = (
        Incident.objects.filter(alerts__node=node).distinct().order_by("-created_at")[:limit]
    )
    return [
        IncidentRow(
            title=incident.title,
            severity=incident.severity,
            status=incident.status,
            color=SEVERITY_COLORS.get(incident.severity, "#6c757d"),
            created_at=incident.created_at,
            url=reverse("admin:alerts_incident_change", args=[incident.pk]),
        )
        for incident in incidents
    ]
```

Check the real ordering field on `Incident` before writing `-created_at`:

```bash
grep -n "created_at\|started_at\|ordering" apps/alerts/models.py | sed -n '1,40p'
```

Use whatever that model actually has.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/_tests/test_node_overview.py
git commit -m "feat(admin): recent incidents panel on the node detail page"
```

---

### Task 7: give render_sparkline a title and axis labels

**Files:**
- Modify: `apps/checkers/admin_charts.py`
- Test: `apps/checkers/_tests/test_admin_charts.py`

The existing signature is
`render_sparkline(points, markers=None, width=120, height=24, pad=2)`. The three
node charts want to be bigger and to say what they are. Every new argument gets a
default that leaves the current callers byte-identical.

**Step 1: Write the failing test**

```python
    def test_defaults_are_unchanged_by_the_new_arguments(self):
        points = [(0, 10.0), (1, 20.0)]
        self.assertEqual(render_sparkline(points), render_sparkline(points, title=""))

    def test_a_title_is_rendered_and_escaped(self):
        svg = render_sparkline([(0, 10.0)], title="Disk <b>usage</b>")
        self.assertIn("&lt;b&gt;", svg)
        self.assertNotIn("<b>", svg)

    def test_axis_labels_show_the_series_range(self):
        svg = render_sparkline([(0, 10.0), (1, 90.0)], show_axis=True)
        self.assertIn("90", svg)
        self.assertIn("10", svg)

    def test_axis_labels_are_off_by_default(self):
        self.assertNotIn("90", render_sparkline([(0, 10.0), (1, 90.0)]))
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/checkers/_tests/test_admin_charts.py -v
```

Expected: FAIL, `unexpected keyword argument 'title'`.

**Step 3: Write minimal implementation**

Add `title: str = ""` and `show_axis: bool = False` to the signature. When
`title` is set, emit a `<text>` element above the plot. When `show_axis` is set,
emit `ymax` and `ymin` as `<text>` at the top-left and bottom-left. Use
`format_html` for both, so the title is escaped and the linters stay quiet. Keep
the no-argument path emitting exactly the markup it emits today — the first test
above is the guard on that.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/checkers/_tests/test_admin_charts.py -v
uv run pytest apps/alerts/_tests/test_node_admin.py -v   # the existing sparkline callers
```

**Step 5: Commit**

```bash
git add apps/checkers/admin_charts.py apps/checkers/_tests/test_admin_charts.py
git commit -m "feat(checkers): sparkline gains an optional title and axis labels"
```

---

### Task 8: the charts panel, honest about peers

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Test: `apps/alerts/_tests/test_node_overview.py`

**Step 1: Write the failing test**

```python
class ChartTests(TestCase):
    def _run(self, checker, metric, value, minutes_ago=0):
        return CheckRun.objects.create(
            checker_name=checker, hostname="hub", status="ok",
            metrics={metric: value},
            executed_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_the_local_node_gets_disk_cpu_and_memory(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", 40.0)
        self._run("cpu", "cpu_percent", 12.0)
        self._run("memory", "memory_percent", 55.0)
        charts = build_charts(node)
        self.assertEqual([c.title for c in charts], ["Disk usage", "CPU", "Memory"])
        self.assertIn("<svg", charts[0].svg)

    def test_a_checker_with_no_history_is_omitted_rather_than_drawn_empty(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", 40.0)
        self.assertEqual([c.title for c in build_charts(node)], ["Disk usage"])

    def test_runs_with_a_non_numeric_metric_are_skipped(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", "n/a", minutes_ago=2)
        self._run("disk", "worst_percent", 40.0, minutes_ago=1)
        charts = build_charts(node)
        self.assertEqual(len(charts), 1)

    def test_a_peer_gets_no_charts(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_charts(node), [])

    def test_a_peer_is_told_why_rather_than_shown_a_blank_chart(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertIn("not pushed to a hub", charts_note(node))

    def test_the_local_node_needs_no_note(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self.assertEqual(charts_note(node), "")
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py::ChartTests -v
```

**Step 3: Write minimal implementation**

```python
# Charts read the same primary metric the state table reads.
CHART_SPECS = [
    ("Disk usage", "disk"),
    ("CPU", "cpu"),
    ("Memory", "memory"),
]

CHART_HISTORY_LIMIT = 50

PEER_HISTORY_NOTE = (
    "Metric history is written by the machine that runs the checker and is "
    "not pushed to a hub, so there is nothing to plot here yet."
)


@dataclass(frozen=True)
class Chart:
    title: str
    svg: object
    latest: str


def build_charts(node) -> list[Chart]:
    """Time series for the local node only.

    A peer has no CheckRun rows on this hub, so it gets an empty list and the
    template shows ``charts_note`` instead. A blank chart would read as "flat",
    which is a lie.
    """
    if not build_identity(node).is_local:
        return []
    charts = []
    for title, checker in CHART_SPECS:
        metric = CHECKER_PRIMARY_METRIC[checker]
        runs = list(
            CheckRun.objects.filter(hostname=node.hostname, checker_name=checker).order_by(
                "-executed_at"
            )[:CHART_HISTORY_LIMIT]
        )
        runs.reverse()  # newest N, restored to oldest -> newest for plotting
        points = []
        markers = []
        for index, run in enumerate(runs):
            value = (run.metrics or {}).get(metric)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            points.append((index, float(value)))
            if run.alert_id is not None:
                markers.append(index)
        if not points:
            continue
        charts.append(
            Chart(
                title=title,
                svg=render_sparkline(
                    points, markers=markers, width=260, height=64,
                    title=title, show_axis=True,
                ),
                latest=_format_metric(points[-1][1]),
            )
        )
    return charts


def charts_note(node) -> str:
    """Why a peer has no charts. Empty for the local node."""
    return "" if build_identity(node).is_local else PEER_HISTORY_NOTE
```

Note the `isinstance(value, bool)` exclusion: `bool` is a subclass of `int` in
Python, and a `True` in a metrics dict must not plot as `1.0`.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/_tests/test_node_overview.py
git commit -m "feat(admin): disk, cpu and memory charts for the local node"
```

---

### Task 9: move preflight and pipeline runs into the overview module

The two remaining readonly fields become panel builders, and preflight gains the
local-only rule the design calls for.

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Test: `apps/alerts/_tests/test_node_overview.py`

**Step 1: Write the failing test**

```python
class PreflightPanelTests(TestCase):
    def test_the_local_node_shows_its_latest_run(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        PreflightRun.objects.create(
            instance_id=local_instance_id(), overall_status="warn",
            passed=9, warnings=2, errors=0,
        )
        panel = build_preflight(node)
        self.assertEqual(panel.run.overall_status, "warn")
        self.assertEqual(panel.note, "")

    def test_the_regression_a_hub_with_no_instance_id_env_var(self):
        # This is the bug that started the whole change: the hub's node row is
        # keyed by the hostname fallback, and the run must match it.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        call_command("preflight", "--json", stdout=StringIO())
        self.assertIsNotNone(build_preflight(node).run)

    def test_the_local_node_with_no_run_yet_says_so(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        panel = build_preflight(node)
        self.assertIsNone(panel.run)
        self.assertIn("No preflight recorded", panel.note)

    def test_a_peer_is_told_preflight_is_node_local(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        panel = build_preflight(node)
        self.assertIsNone(panel.run)
        self.assertIn("node-local", panel.note)


class PipelinePanelTests(TestCase):
    def test_lists_the_ten_newest_runs_with_links(self):
        ...  # port the assertions from test_node_admin.py's recent_pipelines tests

    def test_a_node_with_no_runs_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_pipeline_rows(node), [])
```

Decorate the second test with `@override_settings(INSTANCE_ID="")` and patch
`apps.checkers.preflight.checks._read_file` the way
`apps/checkers/_tests/preflight/test_command.py` does, so it does not touch the
real filesystem.

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py::PreflightPanelTests -v
```

**Step 3: Write minimal implementation**

```python
PEER_PREFLIGHT_NOTE = (
    "Preflight is node-local and is not pushed to a hub, so this hub never "
    "holds a peer's preflight run."
)


@dataclass(frozen=True)
class PreflightPanel:
    run: object
    note: str


def build_preflight(node) -> PreflightPanel:
    """Latest preflight for the local node; an explanation for a peer.

    Matched on ``instance_id`` because PreflightRun has no node FK. Both writers
    now spell this machine the same way — see the 0003 backfill migration for
    the rows written before they did.
    """
    if not build_identity(node).is_local:
        return PreflightPanel(run=None, note=PEER_PREFLIGHT_NOTE)
    run = (
        PreflightRun.objects.filter(instance_id=node.instance_id)
        .order_by("-created_at")
        .first()
    )
    if run is None:
        return PreflightPanel(run=None, note="No preflight recorded on this machine yet.")
    return PreflightPanel(run=run, note="")


@dataclass(frozen=True)
class PipelineRow:
    run_id: str
    origin: str
    status: str
    created_at: object
    url: str


def build_pipeline_rows(node, limit: int = 10) -> list[PipelineRow]:
    """The node's newest pipeline runs, each admin-linked."""
    return [
        PipelineRow(
            run_id=run.run_id,
            origin=run.origin,
            status=run.status,
            created_at=run.created_at,
            url=reverse("admin:orchestration_pipelinerun_change", args=[run.pk]),
        )
        for run in node.pipeline_runs.order_by("-created_at")[:limit]
    ]
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

**Step 5: Commit**

```bash
git add apps/alerts/node_overview.py apps/alerts/_tests/test_node_overview.py
git commit -m "feat(admin): preflight and pipeline panels, preflight scoped to the local node"
```

---

### Task 10: assemble the overview and render it

**Files:**
- Modify: `apps/alerts/node_overview.py`
- Create: `templates/admin/alerts/node/change_form.html`
- Modify: `apps/alerts/admin.py` (NodeAdmin)
- Modify: `apps/alerts/_tests/test_node_admin.py`

**Step 1: Write the failing test**

Replace the readonly-field tests in `apps/alerts/_tests/test_node_admin.py` (the
`disk_sparkline` / `recent_pipelines` / `latest_preflight` block, roughly lines
183 to 283) with page-level tests. Those methods no longer exist; their behaviour
is covered by `test_node_overview.py`.

```python
class NodeChangeFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "ops", "ops@example.com", "pw"
        )
        self.client.force_login(self.user)

    def test_the_overview_renders_above_the_form(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(
            reverse("admin:alerts_node_change", args=[node.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peer")
        self.assertContains(response, "Checker state")
        # and the normal admin form is still there
        self.assertContains(response, 'name="config"')

    def test_the_hubs_own_page_shows_its_preflight(self):
        # The regression: this said "No preflight recorded" for the hub itself.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        PreflightRun.objects.create(
            instance_id=local_instance_id(), overall_status="ok", passed=11
        )
        response = self.client.get(
            reverse("admin:alerts_node_change", args=[node.pk])
        )
        self.assertContains(response, "This hub")
        self.assertNotContains(response, "No preflight recorded")

    def test_a_peer_is_told_why_it_has_no_charts(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(
            reverse("admin:alerts_node_change", args=[node.pk])
        )
        self.assertContains(response, "not pushed to a hub")
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_admin.py::NodeChangeFormTests -v
```

Expected: FAIL — the overview markup is not rendered.

**Step 3: Write minimal implementation**

Add the assembler to `apps/alerts/node_overview.py`:

```python
@dataclass(frozen=True)
class NodeOverview:
    identity: Identity
    chips: object
    checker_rows: list
    incident_rows: list
    charts: list
    charts_note: str
    preflight: PreflightPanel
    pipeline_rows: list


def build_node_overview(node) -> NodeOverview:
    """Every panel on the node detail page, in one object for the template."""
    return NodeOverview(
        identity=build_identity(node),
        chips=render_severity_chips(node),
        checker_rows=build_checker_rows(node),
        incident_rows=build_incident_rows(node),
        charts=build_charts(node),
        charts_note=charts_note(node),
        preflight=build_preflight(node),
        pipeline_rows=build_pipeline_rows(node),
    )
```

In `apps/alerts/admin.py`, on `NodeAdmin`:

- delete the `disk_sparkline`, `recent_pipelines` and `latest_preflight` methods
- remove those three names from `readonly_fields` and `fields`
- add:

```python
    change_form_template = "admin/alerts/node/change_form.html"

    def render_change_form(self, request, context, *args, obj=None, **kwargs):
        """Attach the overview panels; the form below is unchanged."""
        if obj is not None:
            context["node_overview"] = build_node_overview(obj)
        return super().render_change_form(request, context, *args, obj=obj, **kwargs)
```

Import `build_node_overview` from `apps.alerts.node_overview`. Drop the now-unused
`render_sparkline`, `CheckRun` and `PreflightRun` imports if nothing else in
`admin.py` uses them — check with `uv run ruff check apps/alerts`.

Create `templates/admin/alerts/node/change_form.html`:

```html
{% extends "admin/change_form.html" %}

{% block content %}
{% if node_overview %}
<div class="module" style="margin-bottom:20px; padding:12px;">

  <h2 style="margin-top:0;">
    {{ node_overview.identity.role_label }}
    <span style="color:{% if node_overview.identity.freshness_status == 'ok' %}#28a745{% else %}#ffc107{% endif %};">
      &bull; last seen {{ node_overview.identity.freshness_label }}
    </span>
  </h2>
  <p>{{ node_overview.chips }}</p>

  <h3>Checker state</h3>
  {% if node_overview.checker_rows %}
  <table style="width:100%;">
    <thead><tr><th>Checker</th><th>Status</th><th>Value</th><th>Observed</th></tr></thead>
    <tbody>
      {% for row in node_overview.checker_rows %}
      <tr>
        <td><a href="{{ row.url }}">{{ row.checker }}</a></td>
        <td>{{ row.status }}</td>
        <td>{{ row.value }}</td>
        <td>{{ row.observed_at }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>This node has not reported any checker results yet.</p>
  {% endif %}

  <h3>Recent incidents</h3>
  {% if node_overview.incident_rows %}
  <ul>
    {% for row in node_overview.incident_rows %}
    <li>
      <span style="background-color:{{ row.color }}; color:white; padding:2px 6px;
                   border-radius:3px; font-size:11px;">{{ row.severity|upper }}</span>
      <a href="{{ row.url }}">{{ row.title }}</a> &mdash; {{ row.status }} &mdash; {{ row.created_at }}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p>No incidents for this node.</p>
  {% endif %}

  <h3>History</h3>
  {% if node_overview.charts %}
  <div style="display:flex; gap:24px; flex-wrap:wrap;">
    {% for chart in node_overview.charts %}
    <div>{{ chart.svg }}<div style="font-size:11px;">latest {{ chart.latest }}</div></div>
    {% endfor %}
  </div>
  {% else %}
  <p>{{ node_overview.charts_note|default:"No history recorded yet." }}</p>
  {% endif %}

  <h3>Latest preflight</h3>
  {% if node_overview.preflight.run %}
  <p>
    {{ node_overview.preflight.run.created_at }} &mdash;
    <b>{{ node_overview.preflight.run.overall_status }}</b>
    (passed {{ node_overview.preflight.run.passed }},
     warnings {{ node_overview.preflight.run.warnings }},
     errors {{ node_overview.preflight.run.errors }})
  </p>
  {% else %}
  <p>{{ node_overview.preflight.note }}</p>
  {% endif %}

  <h3>Recent pipeline runs</h3>
  {% if node_overview.pipeline_rows %}
  <ul>
    {% for row in node_overview.pipeline_rows %}
    <li><a href="{{ row.url }}">{{ row.run_id }}</a> &mdash; {{ row.origin }}
        &mdash; {{ row.status }} &mdash; {{ row.created_at }}</li>
    {% endfor %}
  </ul>
  {% else %}
  <p>No pipeline runs for this node.</p>
  {% endif %}

</div>
{% endif %}
{{ block.super }}
{% endblock %}
```

`node_overview.chips` and `chart.svg` are `SafeString` from `format_html`, so
Django renders them without escaping. Every other value is autoescaped, which is
what you want for a hostname that arrived over a webhook.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/alerts -q
uv run python manage.py check
```

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/node_overview.py \
        templates/admin/alerts/node/change_form.html \
        apps/alerts/_tests/test_node_admin.py
git commit -m "feat(admin): node detail page renders an operator overview"
```

---

### Task 11: coverage, linters, docs

**Files:**
- Modify: `apps/alerts/AGENTS.md`
- Modify: `apps/checkers/AGENTS.md`

**Step 1: Run the full gate**

```bash
uv run black .
uv run ruff check . --fix
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run bandit -r apps/ config/ -c pyproject.toml
```

Every line you added must be covered, both branches. If `coverage report` shows a
miss in `node_overview.py`, write the test for that branch rather than adding a
pragma.

**Step 2: Document the two behaviours worth knowing**

In `apps/alerts/AGENTS.md`, under the admin conventions, add a short note: the
node detail page is built by `apps/alerts/node_overview.py` and rendered by
`templates/admin/alerts/node/change_form.html`; charts and preflight are
local-node-only because `CheckRun` and `PreflightRun` are written by the machine
that ran them and are never pushed to a hub.

In `apps/checkers/AGENTS.md`, note that `PreflightRun.instance_id` is written
with `local_instance_id()` so it matches the `Node` registry key, and point at
migration `0003` for the rows written before that was true.

**Step 3: Commit**

```bash
git add apps/alerts/AGENTS.md apps/checkers/AGENTS.md
git commit -m "docs: node detail page panels and the preflight identity rule"
```

**Step 4: Verify before you claim done**

REQUIRED SUB-SKILL: use superpowers:verification-before-completion. Open
`/admin/alerts/node/` in a browser, click into the hub's own node, and confirm
the preflight panel names the run you just made. Then click into a peer and
confirm it explains itself rather than showing a blank chart.

{% endraw %}
