---
title: "Hub-side Per-Node Severity Re-evaluation — Implementation Plan"
parent: Plans
---

# Hub-side Per-Node Severity Re-evaluation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the hub recompute an ingested alert's severity against per-node thresholds stored in `Node.config`, so operators can say "only alert CPU > 99% on this node" — without changing anything on the nodes.

**Architecture:** Add a `config` JSON field to `Node`. A new pure-ish `apps/alerts/reevaluation.py` exposes `reevaluate_severity(parsed) -> ParsedAlert` plus a `REEVALUATORS` dispatch registry (one numeric evaluator for the 7 numeric checkers now). `AlertProcessor._process_alert` calls it before create/update. Fail-open: any missing data ⇒ passthrough.

**Tech Stack:** Django model + migration, pytest/pytest-django, `django_json_widget` admin.

**Design doc:** `docs/plans/2026-08-07-hub-node-severity-reeval-design.md`

**Reference before starting:**
- `apps/alerts/services.py` — `AlertProcessor._process_alert` (hook, ~line 198), `_create_alert` (persists `severity=parsed.severity`), `resolve_node(labels)` (Node-by-instance_id helper).
- `apps/alerts/drivers/base.py` — `ParsedAlert` (fields: fingerprint, name, status, severity, labels, annotations, ended_at, …). `annotations` values are strings; cluster metrics live in `annotations["metrics"]` as a JSON string.
- `apps/alerts/drivers/cluster.py` — confirms `labels["checker"]`, `labels["instance_id"]`, and `annotations["metrics"]` (JSON) are populated.
- `apps/alerts/models.py` — `Node` (has `labels` JSONField already).
- Tests: `apps/alerts/_tests/test_services.py`, `test_node_model.py`, `test_node_admin.py`.

**Conventions:** absolute imports; line length 100; black/ruff/bandit clean; 100% branch coverage on changed code; never raise into ingest (fail-open).

---

## Task 1: `Node.config` field + migration

**Files:**
- Modify: `apps/alerts/models.py` (Node)
- Create: migration `apps/alerts/migrations/0007_node_config.py` (via makemigrations)
- Test: `apps/alerts/_tests/test_node_model.py`

**Step 1: Write the failing test**

```python
def test_node_config_defaults_to_empty_dict(self):
    from apps.alerts.models import Node
    node = Node.objects.create(instance_id="web-03")
    self.assertEqual(node.config, {})

def test_node_config_stores_per_checker_thresholds(self):
    from apps.alerts.models import Node
    node = Node.objects.create(
        instance_id="web-03",
        config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
    )
    node.refresh_from_db()
    self.assertEqual(node.config["cpu"]["critical_threshold"], 99)
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_node_model.py -k config -v`
Expected: FAIL — `Node` has no `config`.

**Step 3: Add the field**

In `apps/alerts/models.py`, on `Node` (near `labels`):

```python
config = models.JSONField(
    default=dict,
    blank=True,
    help_text=(
        "Per-checker hub-side policy, keyed by checker name, e.g. "
        '{"cpu": {"warning_threshold": 99, "critical_threshold": 99}}. '
        "Used to re-evaluate alert severity per node."
    ),
)
```

Run: `uv run python manage.py makemigrations alerts` → creates `0007_node_config.py`.

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_node_model.py -k config -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations/0007_node_config.py apps/alerts/_tests/test_node_model.py
git commit -m "feat(alerts): add Node.config for per-node checker policy"
```

---

## Task 2: `reevaluation.py` — numeric evaluator + `reevaluate_severity` (pure path)

**Files:**
- Create: `apps/alerts/reevaluation.py`
- Test: `apps/alerts/_tests/test_reevaluation.py`

**Step 1: Write the failing tests** (evaluator math + metric extraction — no DB yet)

```python
from apps.alerts.drivers.base import ParsedAlert
from apps.alerts.reevaluation import PRIMARY_METRIC, numeric_evaluator


def _alert(checker, metrics_json, severity="critical", status="firing"):
    from datetime import datetime, timezone
    return ParsedAlert(
        fingerprint="fp", name="n", status=status, started_at=datetime.now(timezone.utc),
        severity=severity, labels={"checker": checker, "instance_id": "web-03"},
        annotations={"metrics": metrics_json},
    )


def test_primary_metric_covers_seven_numeric_checkers():
    assert set(PRIMARY_METRIC) == {
        "cpu", "memory", "disk", "disk_inodes", "disk_temp", "cpu_temp", "io_strain"
    }


def test_numeric_evaluator_below_thresholds_is_ok_resolved():
    parsed = _alert("cpu", '{"cpu_percent": 95.2}')
    out = numeric_evaluator(parsed, {"warning_threshold": 99, "critical_threshold": 99})
    assert out == ("info", "resolved")


def test_numeric_evaluator_warning_band():
    parsed = _alert("cpu", '{"cpu_percent": 85}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == ("warning", "firing")


def test_numeric_evaluator_critical():
    parsed = _alert("disk_temp", '{"hottest_c": 70}')
    out = numeric_evaluator(parsed, {"warning_threshold": 60, "critical_threshold": 68})
    assert out == ("critical", "firing")


def test_numeric_evaluator_missing_metric_returns_none():
    parsed = _alert("cpu", '{"other": 1}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_malformed_metrics_returns_none():
    parsed = _alert("cpu", "not json")
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_non_numeric_value_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": "high"}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_missing_thresholds_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80}) is None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -v`
Expected: FAIL — cannot import.

**Step 3: Implement the evaluator layer**

```python
"""Hub-side per-node severity re-evaluation.

Nodes report raw metrics + a default severity; the hub recomputes severity
against per-node policy stored in Node.config. Fail-open: any missing/invalid
input returns the alert unchanged. Never raises into the ingest path.

See docs/plans/2026-08-07-hub-node-severity-reeval-design.md.
"""

import json
import logging
from collections.abc import Callable

from apps.alerts.drivers.base import ParsedAlert

logger = logging.getLogger(__name__)

# checker -> the metric key carrying its primary numeric value
PRIMARY_METRIC = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "disk": "worst_percent",
    "disk_inodes": "worst_percent",
    "disk_temp": "hottest_c",
    "cpu_temp": "hottest_c",
    "io_strain": "busiest_util_percent",
}


def _metrics(parsed: ParsedAlert) -> dict | None:
    raw = (parsed.annotations or {}).get("metrics")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def numeric_evaluator(parsed: ParsedAlert, cfg: dict) -> tuple[str, str] | None:
    """Return (severity, status) for a numeric checker, or None to passthrough."""
    warn = cfg.get("warning_threshold")
    crit = cfg.get("critical_threshold")
    if not isinstance(warn, (int, float)) or not isinstance(crit, (int, float)):
        return None
    checker = (parsed.labels or {}).get("checker", "")
    metrics = _metrics(parsed)
    if metrics is None:
        return None
    value = _number(metrics.get(PRIMARY_METRIC[checker]))
    if value is None:
        return None
    if value >= crit:
        return ("critical", "firing")
    if value >= warn:
        return ("warning", "firing")
    return ("info", "resolved")


# Dispatch seam: checker -> evaluator(parsed, cfg) -> (severity, status) | None.
# First slice: one numeric evaluator for the seven numeric checkers.
REEVALUATORS: dict[str, Callable[[ParsedAlert, dict], "tuple[str, str] | None"]] = {
    checker: numeric_evaluator for checker in PRIMARY_METRIC
}
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/reevaluation.py apps/alerts/_tests/test_reevaluation.py
git commit -m "feat(alerts): numeric severity re-evaluator + dispatch registry"
```

---

## Task 3: `reevaluate_severity` (DB path) + audit

**Files:**
- Modify: `apps/alerts/reevaluation.py`
- Test: `apps/alerts/_tests/test_reevaluation.py` (add a `TestCase` with DB)

**Step 1: Write the failing tests** (DB-backed; use `django.test.TestCase`)

```python
from django.test import TestCase
from apps.alerts.models import Node
from apps.alerts.reevaluation import reevaluate_severity


class ReevaluateSeverityTests(TestCase):
    def _alert(self, checker, metrics_json, instance_id="web-03",
               severity="critical", status="firing", labels=None):
        from datetime import datetime, timezone
        base = {"checker": checker, "instance_id": instance_id}
        if labels is not None:
            base = labels
        return ParsedAlert(
            fingerprint="fp", name="n", status=status,
            started_at=datetime.now(timezone.utc), severity=severity,
            labels=base, annotations={"metrics": metrics_json},
        )

    def test_downgrades_firing_to_resolved(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "info")
        self.assertEqual(out.status, "resolved")
        self.assertIn("severity_reevaluated", out.annotations)

    def test_no_node_config_passthrough(self):
        Node.objects.create(instance_id="web-03")  # empty config
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_unknown_node_passthrough(self):
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")

    def test_non_numeric_checker_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"raid": {"x": 1}})
        out = reevaluate_severity(self._alert("raid", '{"array_count": 1}'))
        self.assertEqual(out.severity, "critical")

    def test_missing_labels_passthrough(self):
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95}', labels={}))
        self.assertEqual(out.severity, "critical")

    def test_no_change_leaves_annotations_untouched(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
        )
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95}'))
        self.assertEqual(out.severity, "critical")  # already critical, still firing
        self.assertNotIn("severity_reevaluated", out.annotations)
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py::ReevaluateSeverityTests -v`
Expected: FAIL — `reevaluate_severity` not defined.

**Step 3: Implement `reevaluate_severity`**

Append to `apps/alerts/reevaluation.py`:

```python
def reevaluate_severity(parsed: ParsedAlert) -> ParsedAlert:
    """Override severity/status from the node's per-checker policy.

    Returns ``parsed`` unchanged when no policy applies. Never raises.
    """
    labels = parsed.labels or {}
    checker = labels.get("checker")
    instance_id = labels.get("instance_id")
    if not checker or not instance_id:
        return parsed

    evaluator = REEVALUATORS.get(checker)
    if evaluator is None:
        return parsed

    from apps.alerts.models import Node

    node = Node.objects.filter(instance_id=instance_id).first()
    if node is None:
        return parsed
    cfg = (node.config or {}).get(checker)
    if not cfg:
        return parsed

    outcome = evaluator(parsed, cfg)
    if outcome is None:
        return parsed

    severity, status = outcome
    if severity == parsed.severity and status == parsed.status:
        return parsed

    original = parsed.severity
    parsed.annotations = dict(parsed.annotations or {})
    parsed.annotations["severity_reevaluated"] = json.dumps(
        {"from": original, "to": severity, "checker": checker, "by": "hub-node-policy"}
    )
    if status == "resolved" and parsed.status != "resolved" and parsed.ended_at is None:
        from django.utils import timezone

        parsed.ended_at = timezone.now()
    parsed.severity = severity
    parsed.status = status
    logger.info(
        "Re-evaluated severity for %s on %s: %s -> %s",
        checker, instance_id, original, severity,
    )
    return parsed
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/reevaluation.py apps/alerts/_tests/test_reevaluation.py
git commit -m "feat(alerts): reevaluate_severity applies per-node policy (fail-open + audit)"
```

---

## Task 4: Wire into `AlertProcessor._process_alert`

**Files:**
- Modify: `apps/alerts/services.py`
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing integration test**

```python
def test_configured_node_alert_persisted_with_reevaluated_severity(self):
    from apps.alerts.models import Alert, Node
    from apps.alerts.services import AlertProcessor
    from apps.alerts.drivers.cluster import ClusterDriver

    Node.objects.create(
        instance_id="web-03",
        config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
    )
    payload = {
        "source": "cluster", "instance_id": "web-03", "hostname": "h",
        "alerts": [{
            "fingerprint": "cpu-web-03", "name": "cpu: high", "status": "firing",
            "severity": "critical", "labels": {"checker": "cpu"},
            "metrics": {"cpu_percent": 95.2},
        }],
    }
    AlertProcessor().process(payload, driver="cluster")
    alert = Alert.objects.get(fingerprint="cpu-web-03")
    self.assertEqual(alert.severity, "info")
    self.assertEqual(alert.status, "resolved")
    self.assertIn("severity_reevaluated", alert.annotations)
```

(Adjust the `process(...)` call to match `AlertProcessor`'s actual entry signature used elsewhere in `test_services.py`.)

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_services.py -k reevaluated -v`
Expected: FAIL — severity still `critical`.

**Step 3: Wire the hook**

In `apps/alerts/services.py`, at the **top** of `_process_alert`:

```python
    def _process_alert(self, parsed, source, result):
        """Process a single parsed alert."""
        from apps.alerts.reevaluation import reevaluate_severity

        parsed = reevaluate_severity(parsed)

        existing = Alert.objects.filter(
            fingerprint=parsed.fingerprint, source=source
        ).first()
        ...
```

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_services.py -k reevaluated -v`
Expected: PASS. Also run the full services test file to confirm no regressions:
`uv run pytest apps/alerts/_tests/test_services.py -q`.

**Step 5: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): re-evaluate per-node severity in AlertProcessor"
```

---

## Task 5: Admin, coverage, lint, docs

**Files:**
- Modify: `apps/alerts/admin.py` (Node admin)
- Test: `apps/alerts/_tests/test_node_admin.py`
- Docs: `apps/alerts/AGENTS.md` (+ `AGENTS.md` if it documents alert severity flow)

**Step 1: Expose `config` in Node admin**

Add `config` to the Node admin (fields/fieldsets) using the JSON widget already used elsewhere (`django_json_widget`), with help text listing the 7 numeric checkers + metric keys. Add/extend a `test_node_admin.py` assertion that `config` is an editable field.

**Step 2: Coverage + lint + security**

```bash
uv run coverage run -m pytest apps/alerts/_tests/test_reevaluation.py apps/alerts/_tests/test_services.py apps/alerts/_tests/test_node_model.py
uv run coverage report -m --include="*/apps/alerts/reevaluation.py,*/apps/alerts/models.py"
uv run black apps/alerts/reevaluation.py apps/alerts/services.py apps/alerts/models.py apps/alerts/admin.py
uv run ruff check apps/alerts/reevaluation.py apps/alerts/services.py apps/alerts/models.py apps/alerts/admin.py --fix
uv run bandit -r apps/alerts/reevaluation.py -c pyproject.toml
```
Expected: 100% branch coverage on `reevaluation.py`; add tests for any uncovered branch. All clean.

**Step 3: Docs**

- `apps/alerts/AGENTS.md`: document that the hub re-evaluates severity per node via `Node.config` + `apps/alerts/reevaluation.py` (the dispatch seam), and that this happens in `AlertProcessor._process_alert`.

**Step 4: Full suite regression + system check**

```bash
uv run python manage.py makemigrations --check --dry-run   # migration committed
uv run pytest apps/alerts/_tests/ -q
uv run python manage.py check
```
Expected: PASS; no missing migration.

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_node_admin.py apps/alerts/AGENTS.md AGENTS.md
git commit -m "feat(alerts): Node.config admin + docs for severity re-evaluation"
```

---

## Acceptance criteria

- `Node.config` persists per-node/per-checker config; editable in admin; migration committed.
- Hub re-evaluates severity for the 7 numeric checkers at ingest against per-node thresholds; overrides node severity/status (incl. firing→resolved).
- Nodes without config are unaffected (backward compatible); re-evaluation is fail-open (never raises).
- Overrides audited via `annotations["severity_reevaluated"]` + a log line.
- 100% branch coverage on `reevaluation.py`; `black`/`ruff`/`bandit`/`pytest` clean; `manage.py check` passes.
