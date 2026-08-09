---
title: "Re-evaluate Existing Alerts on Config Change — Implementation Plan"
parent: Plans
---

# Re-evaluate Existing Alerts on Config Change — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let an operator re-score a node's existing open alerts against its current `Node.config` and apply the outcome (resolve / change severity), from both the Node admin (confirmation dialog) and a `manage.py` command (`--dry-run` + prompt).

**Architecture:** Extract the numeric scoring math into a shared `_score_numeric(...)` in `reevaluation.py`. A new `apps/alerts/reeval_existing.py` selects a node's firing alerts, re-scores them from their stored metrics, and (on apply) updates alerts + `AlertHistory` + a distinct audit annotation and auto-resolves incidents — all in a transaction. Two thin surfaces call it: a `DjangoObjectActions` admin action with a confirm template, and a management command.

**Tech Stack:** Django admin (`django_object_actions`), management command, pytest.

**Design doc:** `docs/plans/2026-08-08-reeval-existing-alerts-design.md`

**Reference before starting:**
- `apps/alerts/reevaluation.py` — `numeric_evaluator`, `PRIMARY_METRIC`, `REEVALUATORS`, `_metrics`, `_number`.
- `apps/alerts/services.py` — `_update_alert` (~266; resolve pattern: status/ended_at/AlertHistory), `_check_incident_resolution` (~371; incident auto-resolve).
- `apps/alerts/models.py` — `Alert` (status, severity, ended_at, labels, annotations, node FK), `AlertHistory` (event ≤50 chars, old_status/new_status), `Incident.resolve(summary=...)`, `Node.config`.
- `apps/alerts/admin.py` — `IncidentAdmin` (`DjangoObjectActions` + `@object_action` pattern), `NodeAdmin` (~491).
- `apps/alerts/management/commands/push_to_hub.py` — command style.
- `templates/admin/dashboard.html` — admin template location.

**Conventions:** absolute imports; line length 100; black/ruff/bandit clean; 100% branch coverage on new code.

---

## Task 1: Extract `_score_numeric` in `reevaluation.py` (no behavior change)

**Files:**
- Modify: `apps/alerts/reevaluation.py`
- Test: `apps/alerts/_tests/test_reevaluation.py` (existing tests must still pass)

**Step 1: Write the failing test**

```python
def test_score_numeric_is_the_shared_scorer():
    from apps.alerts.reevaluation import _score_numeric
    # value >= crit -> critical; between -> warning; below -> info/resolved
    assert _score_numeric("cpu", {"cpu_percent": 99}, {"warning_threshold": 90, "critical_threshold": 95}) == ("critical", "firing", 99.0)
    assert _score_numeric("cpu", {"cpu_percent": 92}, {"warning_threshold": 90, "critical_threshold": 95}) == ("warning", "firing", 92.0)
    assert _score_numeric("cpu", {"cpu_percent": 50}, {"warning_threshold": 90, "critical_threshold": 95}) == ("info", "resolved", 50.0)
    # fail-open cases
    assert _score_numeric("cpu", {"cpu_percent": 99}, {"warning_threshold": True, "critical_threshold": True}) is None
    assert _score_numeric("cpu", {"cpu_percent": 99}, {"warning_threshold": 90, "critical_threshold": 50}) is None
    assert _score_numeric("cpu", {"other": 1}, {"warning_threshold": 90, "critical_threshold": 95}) is None
    assert _score_numeric("unknown", {"x": 1}, {"warning_threshold": 90, "critical_threshold": 95}) is None
    assert _score_numeric("cpu", {"cpu_percent": 99}, "not-a-dict") is None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -k score_numeric -v`
Expected: FAIL — no `_score_numeric`.

**Step 3: Refactor**

Extract the math into `_score_numeric`, and make `numeric_evaluator` delegate:

```python
def _score_numeric(checker: str, metrics: dict, cfg) -> tuple[str, str, float] | None:
    """Pure scorer shared by ingest and config-change re-evaluation."""
    if not isinstance(cfg, dict):
        return None
    warn = _number(cfg.get("warning_threshold"))
    crit = _number(cfg.get("critical_threshold"))
    if warn is None or crit is None or crit < warn:
        return None
    metric_key = PRIMARY_METRIC.get(checker)
    if metric_key is None or not isinstance(metrics, dict):
        return None
    value = _number(metrics.get(metric_key))
    if value is None:
        return None
    if value >= crit:
        return ("critical", "firing", value)
    if value >= warn:
        return ("warning", "firing", value)
    return ("info", "resolved", value)


def numeric_evaluator(parsed: ParsedAlert, cfg: dict) -> tuple[str, str, float] | None:
    """Return (severity, status, value) for a numeric checker, or None to passthrough."""
    metrics = _metrics(parsed)
    if metrics is None:
        return None
    checker = (parsed.labels or {}).get("checker", "")
    return _score_numeric(checker, metrics, cfg)
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -v` (existing + new)
Expected: PASS — all existing `numeric_evaluator`/`reevaluate_severity` tests still green.

**Step 5: Commit**

```bash
git add apps/alerts/reevaluation.py apps/alerts/_tests/test_reevaluation.py
git commit -m "refactor(alerts): extract _score_numeric shared scorer"
```

---

## Task 2: Core `reeval_existing.py` (preview + apply)

**Files:**
- Create: `apps/alerts/reeval_existing.py`
- Test: `apps/alerts/_tests/test_reeval_existing.py`

**Step 1: Write the failing tests** (Django `TestCase`, DB-backed)

```python
import json
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.alerts.reeval_existing import (
    apply_node_alert_reeval,
    preview_node_alert_reeval,
)


class ReevalExistingTests(TestCase):
    def _node(self, cfg):
        return Node.objects.create(instance_id="web-03", config=cfg)

    def _alert(self, checker, value, severity="critical", status="firing", metric="cpu_percent"):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03", source="cluster", name=f"{checker} high",
            severity=severity, status=status,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({metric: value})},
        )

    def test_preview_reports_resolution_without_writing(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert("cpu", 95.2)
        report = preview_node_alert_reeval(node)
        self.assertEqual(len(report.changes), 1)
        self.assertEqual(report.changes[0].new_status, "resolved")
        a.refresh_from_db()
        self.assertEqual(a.status, "firing")  # preview did NOT write

    def test_apply_resolves_and_audits(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert("cpu", 95.2)
        report = apply_node_alert_reeval(node)
        self.assertEqual(report.resolved_count, 1)
        a.refresh_from_db()
        self.assertEqual(a.status, "resolved")
        self.assertEqual(a.severity, "info")
        self.assertIsNotNone(a.ended_at)
        self.assertIn("reevaluated_on_config_change", a.annotations)
        self.assertNotIn("severity_reevaluated", a.annotations)  # distinct key
        self.assertTrue(AlertHistory.objects.filter(alert=a, new_status="resolved").exists())

    def test_apply_changes_severity_when_still_firing(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        a = self._alert("cpu", 85)  # was critical, now warning (>=80, <99)
        apply_node_alert_reeval(node)
        a.refresh_from_db()
        self.assertEqual(a.severity, "warning")
        self.assertEqual(a.status, "firing")
        self.assertIsNone(a.ended_at)

    def test_skips_when_no_config_no_metrics_or_unchanged(self):
        node = self._node({})  # no config
        self._alert("cpu", 95.2)
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_skips_non_numeric_checker(self):
        node = self._node({"raid": {"warning_threshold": 1, "critical_threshold": 2}})
        self._alert("raid", 1, metric="array_count")
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_incident_auto_resolves_when_last_alert_resolves(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert("cpu", 95.2)
        inc = Incident.objects.create(title="t", severity="critical", status="open")
        inc.alerts.add(a)
        apply_node_alert_reeval(node)
        inc.refresh_from_db()
        self.assertEqual(inc.status, "resolved")

    def test_apply_is_idempotent(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert("cpu", 95.2)
        apply_node_alert_reeval(node)
        second = apply_node_alert_reeval(node)
        self.assertEqual(second.changes, [])
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reeval_existing.py -v`
Expected: FAIL — module missing.

**Step 3: Implement**

```python
"""Re-evaluate a node's existing open alerts against its current Node.config.

Operator-triggered (admin action + management command). Re-scores stored alert
metrics with the same scorer as ingest, then (on apply) resolves / adjusts
severity, records history + a distinct audit annotation, and auto-resolves
incidents. See docs/plans/2026-08-08-reeval-existing-alerts-design.md.
"""

import json
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus, Node
from apps.alerts.reevaluation import REEVALUATORS, _score_numeric

logger = logging.getLogger(__name__)


@dataclass
class AlertChange:
    alert: Alert
    old_severity: str
    old_status: str
    new_severity: str
    new_status: str
    value: float


@dataclass
class ReevalReport:
    node: Node
    changes: list[AlertChange] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status == "resolved")

    @property
    def severity_changed_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status != "resolved")


def _metrics_of(alert: Alert) -> dict | None:
    raw = (alert.annotations or {}).get("metrics")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _score_alert(alert: Alert, config: dict) -> tuple[str, str, float] | None:
    checker = (alert.labels or {}).get("checker", "")
    if checker not in REEVALUATORS:
        return None
    cfg = (config or {}).get(checker)
    metrics = _metrics_of(alert)
    if metrics is None:
        return None
    return _score_numeric(checker, metrics, cfg)


def preview_node_alert_reeval(node: Node) -> ReevalReport:
    """Report which of the node's open alerts would change; no writes."""
    report = ReevalReport(node=node)
    open_alerts = Alert.objects.filter(node=node, status="firing")
    for alert in open_alerts:
        outcome = _score_alert(alert, node.config)
        if outcome is None:
            continue
        new_sev, new_status, value = outcome
        if new_sev == alert.severity and new_status == alert.status:
            continue
        report.changes.append(
            AlertChange(
                alert=alert,
                old_severity=alert.severity,
                old_status=alert.status,
                new_severity=new_sev,
                new_status=new_status,
                value=value,
            )
        )
    return report


@transaction.atomic
def apply_node_alert_reeval(node: Node) -> ReevalReport:
    """Apply the re-score: update alerts, history, audit, and incidents."""
    report = preview_node_alert_reeval(node)
    for change in report.changes:
        alert = change.alert
        alert.severity = change.new_severity
        alert.status = change.new_status
        if change.new_status == "resolved":
            alert.ended_at = alert.ended_at or timezone.now()
            event = "resolved"
        else:
            alert.ended_at = None
            event = "reevaluated"
        alert.annotations = dict(alert.annotations or {})
        alert.annotations["reevaluated_on_config_change"] = json.dumps(
            {
                "from": change.old_severity,
                "to": change.new_severity,
                "status_from": change.old_status,
                "status_to": change.new_status,
                "value": change.value,
                "thresholds": (node.config or {}).get(
                    (alert.labels or {}).get("checker", ""), {}
                ),
                "checker": (alert.labels or {}).get("checker", ""),
                "by": "hub-node-policy:config-change",
                "at": timezone.now().isoformat(),
            }
        )
        alert.save()
        AlertHistory.objects.create(
            alert=alert,
            event=event,
            old_status=change.old_status,
            new_status=change.new_status,
        )
    _resolve_incidents_for(node)
    if report.changes:
        logger.info(
            "Config-change re-eval on %s: resolved %d, changed severity on %d",
            node.instance_id,
            report.resolved_count,
            report.severity_changed_count,
        )
    return report


def _resolve_incidents_for(node: Node) -> None:
    """Resolve open/ack incidents (touching this node) whose alerts all resolved."""
    incidents = Incident.objects.filter(
        status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        alerts__node=node,
    ).distinct()
    for incident in incidents:
        if incident.alerts.exists() and not incident.alerts.filter(status="firing").exists():
            incident.resolve(summary="All alerts resolved by config-change re-evaluation")
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_reeval_existing.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/reeval_existing.py apps/alerts/_tests/test_reeval_existing.py
git commit -m "feat(alerts): re-evaluate a node's existing open alerts (preview + apply)"
```

---

## Task 3: Admin action + confirmation template

**Files:**
- Modify: `apps/alerts/admin.py` (NodeAdmin → add `DjangoObjectActions`, action)
- Create: `templates/admin/alerts/node/reevaluate_confirm.html`
- Test: `apps/alerts/_tests/test_node_admin.py`

**Step 1: Write the failing test**

```python
def test_reevaluate_action_present(self):
    self.assertIn("reevaluate_open_alerts", self._admin().change_actions)

def test_reevaluate_empty_report_messages(self):
    # Node with no matching alerts -> action returns None (back to change form) with a message.
    ...
```
(Also add a request-driven test: a firing cpu alert under crit=99, GET the action → response renders the alert; POST with confirm → alert resolved. Use `RequestFactory` + an admin user, mirroring how other admin action tests in the repo drive `object_action` methods.)

**Step 2: Run to verify it fails** — action not present.

**Step 3: Implement**

Make `NodeAdmin` extend `DjangoObjectActions`, add `change_actions = ["reevaluate_open_alerts"]`, and:

```python
from django.template.response import TemplateResponse
from apps.alerts.reeval_existing import apply_node_alert_reeval, preview_node_alert_reeval

@object_action(label="Re-evaluate open alerts",
               description="Re-score this node's open alerts against its current config")
def reevaluate_open_alerts(self, request, obj):
    report = preview_node_alert_reeval(obj)
    if not report.changes:
        self.message_user(request, "No open alerts need re-evaluation.")
        return
    if request.method == "POST" and request.POST.get("confirm"):
        applied = apply_node_alert_reeval(obj)
        self.message_user(
            request,
            f"Resolved {applied.resolved_count}; changed severity on "
            f"{applied.severity_changed_count}.",
        )
        return
    return TemplateResponse(
        request,
        "admin/alerts/node/reevaluate_confirm.html",
        {**self.admin_site.each_context(request), "node": obj, "report": report,
         "title": "Confirm re-evaluation", "opts": self.model._meta},
    )
```

Template `templates/admin/alerts/node/reevaluate_confirm.html` extends
`admin/base_site.html`: a table of `report.changes`
(checker, `old_severity/old_status → new_severity/new_status`, value) and a form
POSTing `confirm=1` back to the action URL, plus a Cancel link to the change page.

**Step 4: Run to verify it passes** — admin tests green.

**Step 5: Commit**

```bash
git add apps/alerts/admin.py templates/admin/alerts/node/reevaluate_confirm.html apps/alerts/_tests/test_node_admin.py
git commit -m "feat(alerts): Node admin action to re-evaluate open alerts (confirm dialog)"
```

---

## Task 4: Management command

**Files:**
- Create: `apps/alerts/management/commands/reevaluate_node_alerts.py`
- Test: `apps/alerts/_tests/commands/test_reevaluate_node_alerts.py`

**Step 1: Write the failing tests**

```python
# --dry-run previews without writing; --noinput applies; unknown instance_id errors;
# plain invocation with 'n' at the prompt does not apply, 'y' applies.
```
Drive via `call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=out)`, and for the prompt, patch `builtins.input` to return "y"/"n".

**Step 2: Run to verify it fails.**

**Step 3: Implement**

```python
class Command(BaseCommand):
    help = "Re-evaluate a node's existing open alerts against its current config."

    def add_arguments(self, parser):
        parser.add_argument("instance_id")
        parser.add_argument("--dry-run", action="store_true", help="Preview only.")
        parser.add_argument("--noinput", action="store_true", help="Apply without prompting.")

    def handle(self, *args, **options):
        node = Node.objects.filter(instance_id=options["instance_id"]).first()
        if node is None:
            raise CommandError(f"No node with instance_id '{options['instance_id']}'")
        report = preview_node_alert_reeval(node)
        self._print_report(report)
        if options["dry_run"] or not report.changes:
            return
        if not options["noinput"]:
            if input("Apply these changes? [y/N] ").strip().lower() != "y":
                self.stdout.write("Aborted.")
                return
        applied = apply_node_alert_reeval(node)
        self.stdout.write(self.style.SUCCESS(
            f"Resolved {applied.resolved_count}; changed severity on "
            f"{applied.severity_changed_count}."))
```
`_print_report` prints one line per change: `checker: old_sev/old_status -> new_sev/new_status (value)`.

**Step 4: Run to verify it passes.**

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/reevaluate_node_alerts.py apps/alerts/_tests/commands/test_reevaluate_node_alerts.py
git commit -m "feat(alerts): reevaluate_node_alerts management command (--dry-run + prompt)"
```

---

## Task 5: Coverage, lint, docs

**Step 1: Coverage**

```bash
uv run coverage run -m pytest apps/alerts/_tests/test_reeval_existing.py apps/alerts/_tests/test_reevaluation.py apps/alerts/_tests/test_node_admin.py apps/alerts/_tests/commands/test_reevaluate_node_alerts.py
uv run coverage report -m --include="*/apps/alerts/reeval_existing.py,*/apps/alerts/reevaluation.py"
```
Expected: 100% branch coverage on `reeval_existing.py` + `reevaluation.py`. Add tests for any uncovered branch (e.g. `_metrics_of` malformed/None, incident with no alerts, still-firing incident not resolved).

**Step 2: Lint/format/security/type**

```bash
uv run black apps/alerts/reeval_existing.py apps/alerts/admin.py apps/alerts/management/commands/reevaluate_node_alerts.py apps/alerts/reevaluation.py apps/alerts/_tests/
uv run ruff check apps/alerts/ --fix
uv run mypy apps/alerts/reeval_existing.py
uv run bandit -r apps/alerts/reeval_existing.py apps/alerts/management/commands/reevaluate_node_alerts.py -c pyproject.toml
```

**Step 3: Docs**

- `apps/alerts/AGENTS.md`: note the config-change re-eval (`reeval_existing.py`, the admin action, and the `reevaluate_node_alerts` command), distinct from ingest-time re-eval.

**Step 4: Full suite + system check**

```bash
uv run pytest apps/alerts/_tests/ -q
uv run python manage.py check
```

**Step 5: Commit**

```bash
git add apps/alerts/AGENTS.md
git commit -m "docs(alerts): document config-change alert re-evaluation"
```

---

## Acceptance criteria

- `preview_node_alert_reeval` / `apply_node_alert_reeval` re-score a node's open alerts via the shared `_score_numeric`; apply resolves/adjusts severity, writes `AlertHistory` + `reevaluated_on_config_change` audit, and auto-resolves incidents — in a transaction; idempotent.
- Admin action shows a confirmation preview and applies only on confirm; CLI supports `--dry-run` and a confirm prompt (`--noinput` to skip).
- Existing-firing-only scope; no ingest-time behavior change (existing re-eval tests still pass).
- 100% branch coverage on new code; `black`/`ruff`/`bandit`/`pytest` clean; `manage.py check` passes.
