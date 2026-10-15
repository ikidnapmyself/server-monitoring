---
title: "Sysadmin Console PR 1: Identity Keys"
parent: Plans
---

# Sysadmin Console PR 1: Identity Keys Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Incidents group on machine keys `(subject, instance)` instead of the display string
`alert.name`, and a skipped check reports `skipped` instead of `ok`.

**Architecture:** `Alert.subject` is derived from data already on the alert, the `checker`
label or `source:name`, by one helper in `apps/alerts/identity.py`. `Incident` gains
`subject`, `instance` and `node`, stamped when the incident is created, and
`_find_open_incident` becomes one indexed query. A backfill migration fills the new
columns on existing rows, so no data is reset. `CheckStatus.SKIPPED` joins both status
enums and maps to the same alert outcome as `OK`.

**Tech Stack:** Django 5, pytest + pytest-django, `uv`.

Design: `docs/plans/2026-10-15-sysadmin-console-foundation-design.md`, section 1.

**Changes from the design, decided while planning:**

- Dropping `Incident.title` and `Incident.description` moves to PR 2. About 60 files read
  them, and their replacement is `describe_incident`, which PR 2 builds. PR 1 keeps
  writing both columns unchanged.
- No data reset. `subject`, `instance` and `node` are derivable from every existing row,
  so migration 0012 backfills them the way 0011 did.
- Checker alerts keep writing `<CHECKER> Check Alert` as their name until PR 2 gives them
  a describer. After this PR the name is display-only.

**Grouping change to know about:** two webhook sources sending the same alert name for the
same host used to share an incident. They now get one each, because the subject includes
the source. Checker alerts group exactly as before.

**Base branch:** `docs/sysadmin-console-foundation`, which is stacked on
`feat/console-primer`. Implementation goes on `feat/console-identity-keys` cut from it.

---

### Task 1: The subject helper

**Files:**
- Modify: `apps/alerts/identity.py`
- Test: `apps/alerts/_tests/test_identity.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_identity.py`:

```python
from django.test import SimpleTestCase

from apps.alerts.identity import alert_subject


class AlertSubjectTests(SimpleTestCase):
    def test_checker_label_names_the_subject(self):
        self.assertEqual(
            alert_subject("cluster", "CPU Check Alert", {"checker": "cpu"}), "checker:cpu"
        )

    def test_without_checker_label_source_and_name(self):
        self.assertEqual(
            alert_subject("alertmanager", "HighCPU", {"instance": "web-1"}),
            "alertmanager:HighCPU",
        )

    def test_blank_checker_label_falls_back(self):
        self.assertEqual(alert_subject("grafana", "X", {"checker": ""}), "grafana:X")

    def test_non_string_checker_label_falls_back(self):
        self.assertEqual(alert_subject("grafana", "X", {"checker": ["cpu"]}), "grafana:X")

    def test_non_dict_labels_do_not_raise(self):
        self.assertEqual(alert_subject("generic", "X", "pwned"), "generic:X")

    def test_capped_at_column_length(self):
        self.assertEqual(len(alert_subject("generic", "x" * 400, {})), 255)
```

**Step 2: Run to verify failure**

Run: `uv run pytest apps/alerts/_tests/test_identity.py -k AlertSubject -q`
Expected: FAIL with `ImportError: cannot import name 'alert_subject'`.

**Step 3: Implement**

Add to `apps/alerts/identity.py`:

```python
SUBJECT_MAX_LENGTH = 255


def alert_subject(source: str, name: str, labels) -> str:
    """What kind of problem an alert reports, as a machine key.

    Incidents group on this and the instance, never on display text. A checker
    alert is named by its checker. Anything else by its source and the name that
    source gave it. Labels are untrusted webhook input and may not be a dict.
    """
    labels = labels if isinstance(labels, dict) else {}
    checker = labels.get("checker")
    if isinstance(checker, str) and checker:
        return f"checker:{checker}"[:SUBJECT_MAX_LENGTH]
    return f"{source}:{name}"[:SUBJECT_MAX_LENGTH]
```

**Step 4: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_identity.py -q`
Expected: all pass.

**Step 5: Commit**

```bash
git add apps/alerts/identity.py apps/alerts/_tests/test_identity.py
git commit -m "feat(alerts): derive an alert's subject from its checker or source and name"
```

---

### Task 2: Schema and backfill

**Files:**
- Modify: `apps/alerts/models.py` (`Alert` near `context_key`, `Incident` fields and `Meta.indexes`)
- Create: `apps/alerts/migrations/0012_subject_keys.py`
- Test: `apps/alerts/_tests/test_subject_migration.py`

**Step 1: Add the fields**

In `Alert`, after `context_key`:

```python
    subject = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="What kind of problem this is, as a machine key. See apps.alerts.identity.",
    )
```

In `Incident`, after `severity`:

```python
    subject = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Subject of the alerts this incident groups. Grouping key with instance.",
    )
    instance = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Host the incident is about, from its alerts' labels. Grouping key with subject.",
    )
    node = models.ForeignKey(
        "alerts.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incidents",
        help_text="Registered node the incident is about, when its alerts name one.",
    )
```

Add to `Incident.Meta.indexes`:

```python
            models.Index(fields=["subject", "instance", "status"]),
```

**Step 2: Generate the schema migration**

Run: `uv run python manage.py makemigrations alerts -n subject_keys`
Expected: `apps/alerts/migrations/0012_subject_keys.py` with three `AddField`s on the two
models and one `AddIndex`.

**Step 3: Write the failing backfill test**

Create `apps/alerts/_tests/test_subject_migration.py`, following
`test_identity_migration.py`'s stand-in-`apps` pattern:

```python
"""Tests for the subject backfill (migration 0012)."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node

MIGRATION = import_module("apps.alerts.migrations.0012_subject_keys")
forward = MIGRATION.forward


class _Apps:
    def get_model(self, app_label, model_name):
        return django_apps.get_model(app_label, model_name)


def _alert(**kw):
    defaults = dict(
        fingerprint="fp",
        source="cluster",
        name="CPU Check Alert",
        severity="warning",
        status="firing",
        labels={},
        started_at=timezone.now(),
    )
    defaults.update(kw)
    return Alert.objects.create(**defaults)


class SubjectBackfillTests(TestCase):
    def test_checker_alert_gets_checker_subject(self):
        alert = _alert(labels={"checker": "cpu", "instance_id": "n1"})
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "checker:cpu")

    def test_webhook_alert_gets_source_and_name(self):
        alert = _alert(source="alertmanager", name="HighCPU", labels={"instance": "w1"})
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "alertmanager:HighCPU")

    def test_incident_takes_its_oldest_alerts_keys(self):
        node = Node.objects.create(instance_id="n1", hostname="h1")
        incident = Incident.objects.create(title="t", severity="warning")
        _alert(fingerprint="a", labels={"checker": "cpu", "instance_id": "n1"},
               node=node, incident=incident)
        _alert(fingerprint="b", labels={"checker": "cpu", "instance_id": "n2"},
               incident=incident)
        forward(_Apps(), None)
        incident.refresh_from_db()
        self.assertEqual(
            (incident.subject, incident.instance, incident.node_id),
            ("checker:cpu", "n1", node.pk),
        )

    def test_incident_without_alerts_is_left_blank(self):
        incident = Incident.objects.create(title="t", severity="warning")
        forward(_Apps(), None)
        incident.refresh_from_db()
        self.assertEqual((incident.subject, incident.instance), ("", ""))

    def test_non_dict_labels_do_not_raise(self):
        alert = _alert(source="generic", name="X", labels="pwned")
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "generic:X")
```

Run: `uv run pytest apps/alerts/_tests/test_subject_migration.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'forward'`.

**Step 4: Add the backfill to the migration**

At the top of `0012_subject_keys.py`, above `class Migration`, add frozen copies of the two
helpers. 0011's header explains why migrations never import live identity code:

```python
# Frozen copies of apps.alerts.identity.alert_subject and
# apps.alerts.services.instance_key_from_labels as of 2026-10-15. A migration must
# replay the same on a fresh database next year, so it never imports live code.
def _subject(source, name, labels):
    labels = labels if isinstance(labels, dict) else {}
    checker = labels.get("checker")
    if isinstance(checker, str) and checker:
        return f"checker:{checker}"[:255]
    return f"{source}:{name}"[:255]


def _instance(labels):
    labels = labels if isinstance(labels, dict) else {}
    return labels.get("instance_id") or labels.get("instance") or labels.get("hostname") or ""


def forward(apps, schema_editor):
    Alert = apps.get_model("alerts", "Alert")
    Incident = apps.get_model("alerts", "Incident")
    for alert in Alert.objects.only("pk", "source", "name", "labels").iterator():
        Alert.objects.filter(pk=alert.pk).update(
            subject=_subject(alert.source, alert.name, alert.labels)
        )
    for incident in Incident.objects.only("pk").iterator():
        first = (
            Alert.objects.filter(incident_id=incident.pk)
            .order_by("received_at", "pk")
            .only("subject", "labels", "node_id")
            .first()
        )
        if first is None:
            continue
        Incident.objects.filter(pk=incident.pk).update(
            subject=first.subject,
            instance=_instance(first.labels)[:255],
            node_id=first.node_id,
        )
```

Append to `operations`:

```python
        migrations.RunPython(forward, migrations.RunPython.noop),
```

**Step 5: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_subject_migration.py -q && uv run python manage.py migrate alerts`
Expected: tests pass, migration applies.

**Step 6: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations/0012_subject_keys.py apps/alerts/_tests/test_subject_migration.py
git commit -m "feat(alerts): add subject keys to alerts and incidents, backfilled from existing rows"
```

---

### Task 3: Write the subject on every alert write

**Files:**
- Modify: `apps/alerts/services.py` (`_create_alert`, `_update_alert`)
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing tests**

Add a class to `apps/alerts/_tests/test_services.py`, reusing `IncidentGroupingTests._am`'s
Alertmanager payload shape:

```python
class AlertSubjectWriteTests(TestCase):
    def setUp(self):
        self.orchestrator = AlertOrchestrator()

    def _am(self, name="HighCPU", status="firing"):
        return {
            "version": "4",
            "receiver": "webhook",
            "status": status,
            "alerts": [
                {
                    "status": status,
                    "labels": {"alertname": name, "severity": "warning", "instance": "w1"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-subject",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def test_created_alert_carries_subject(self):
        self.orchestrator.process_webhook(self._am(), driver="alertmanager")
        self.assertEqual(Alert.objects.get().subject, "alertmanager:HighCPU")

    def test_updated_alert_follows_a_renamed_source_alert(self):
        self.orchestrator.process_webhook(self._am(), driver="alertmanager")
        self.orchestrator.process_webhook(self._am(name="HighCPU2"), driver="alertmanager")
        self.assertEqual(Alert.objects.get().subject, "alertmanager:HighCPU2")
```

Run: `uv run pytest apps/alerts/_tests/test_services.py -k AlertSubjectWrite -q`
Expected: FAIL, subject is `""`.

**Step 2: Implement**

In `apps/alerts/services.py` import `alert_subject` from `apps.alerts.identity`. In
`_create_alert`, add to `Alert.objects.create(...)`:

```python
            subject=alert_subject(source, parsed.name, parsed.labels),
```

In `_update_alert`, beside `alert.name = parsed.name`:

```python
        alert.subject = alert_subject(alert.source, parsed.name, parsed.labels)
```

**Step 3: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_services.py -q`
Expected: all pass.

**Step 4: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): stamp the subject on every alert write"
```

---

### Task 4: Group incidents on (subject, instance)

**Files:**
- Modify: `apps/alerts/services.py` (`incident_instance_key` docstring, `_create_or_attach_incident`, `_find_open_incident`, the refire comment above `sibling = self._find_open_incident(alert)`)
- Test: `apps/alerts/_tests/test_services.py` (`IncidentGroupingTests`)

**Step 1: Write the failing tests**

Add to `IncidentGroupingTests`:

```python
    def test_new_incident_carries_subject_instance_and_node(self):
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        inc = Incident.objects.get()
        self.assertEqual((inc.subject, inc.instance), ("alertmanager:HighCPU", "web-03"))

    def test_same_name_from_two_sources_do_not_merge(self):
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        Incident.objects.update(subject="grafana:HighCPU")
        self.orchestrator.process_webhook(self._am("fp2", "web-03"), driver="alertmanager")
        self.assertEqual(Incident.objects.count(), 2)

    def test_grouping_ignores_display_name(self):
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        Alert.objects.update(name="renamed for display")
        self.orchestrator.process_webhook(self._am("fp2", "web-03"), driver="alertmanager")
        self.assertEqual(Incident.objects.count(), 1)

    def test_grouping_is_one_query(self):
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        alert = Alert.objects.get()
        with self.assertNumQueries(1):
            self.orchestrator._find_open_incident(alert)
```

Add a checker-origin case so `node` is covered:

```python
    def test_checker_incident_links_its_node(self):
        from apps.alerts.models import Node

        node = Node.objects.create(instance_id="n1", hostname="h1")
        payload = {
            "source": "cluster",
            "instance_id": "n1",
            "hostname": "h1",
            "alerts": [
                {
                    "name": "CPU Check Alert",
                    "status": "firing",
                    "severity": "warning",
                    "labels": {"checker": "cpu", "instance_id": "n1"},
                }
            ],
        }
        self.orchestrator.process_webhook(payload, driver="cluster")
        inc = Incident.objects.get()
        self.assertEqual((inc.subject, inc.node_id), ("checker:cpu", node.pk))
```

If the cluster payload shape is rejected, copy a passing payload from
`apps/alerts/_tests/drivers/` for `ClusterDriver` instead of guessing.

Rename `test_no_instance_label_groups_by_name_only` to
`test_no_instance_label_groups_by_subject_only` and update the class docstring to
`(subject, instance)`.

Run: `uv run pytest apps/alerts/_tests/test_services.py -k IncidentGrouping -q`
Expected: the new tests FAIL.

**Step 2: Implement**

Replace `_find_open_incident`:

```python
    def _find_open_incident(self, alert) -> "Incident | None":
        """Open incident about the same subject on the same instance."""
        return Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
            subject=alert.subject,
            instance=incident_instance_key(alert)[:255],
        ).first()
```

In `_create_or_attach_incident`, stamp the keys on create:

```python
        incident = Incident.objects.create(
            title=alert.name,
            severity=alert.severity,
            description=alert.description,
            subject=alert.subject,
            instance=incident_instance_key(alert)[:255],
            node=alert.node,
        )
```

Rewrite its docstring and the refire comment to say `(subject, instance)` where they say
`(name, instance)`. In `incident_instance_key`'s docstring, change "falls back to
name-only" to "falls back to subject-only".

**Step 3: Run the alerts suite**

Run: `uv run pytest apps/alerts -q`
Expected: all pass. A failure from a test that builds an `Incident` by hand and expects a
later alert to join it by name means that test needs `subject` and `instance` set on its
fixture. Set them, don't restore name matching.

**Step 4: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): group incidents on subject and instance, never on the alert name"
```

---

### Task 5: `CheckStatus.SKIPPED`

**Files:**
- Modify: `apps/checkers/checkers/base.py` (`CheckStatus`)
- Modify: `apps/checkers/models.py` (`CheckStatus` TextChoices)
- Create: `apps/checkers/migrations/0004_checkrun_status_skipped.py` (generated)
- Modify: `apps/alerts/check_integration.py` (`STATUS_TO_SEVERITY`, `STATUS_TO_ALERT_STATUS`)
- Modify: `apps/alerts/management/commands/push_to_hub.py` (`_result_to_alert`)
- Modify: `config/admin_badges.py` (the check-status tone table holding `"unknown": "muted"`)
- Test: `apps/alerts/_tests/test_check_integration.py`, `apps/alerts/_tests/commands/` (the push_to_hub test file), `apps/checkers/_tests/`

**Step 1: Write the failing tests**

In `apps/alerts/_tests/test_check_integration.py`:

```python
class SkippedStatusTests(TestCase):
    def test_skipped_maps_like_ok(self):
        from apps.alerts.check_integration import STATUS_TO_ALERT_STATUS, STATUS_TO_SEVERITY
        from apps.checkers.checkers.base import CheckStatus

        self.assertEqual(STATUS_TO_ALERT_STATUS[CheckStatus.SKIPPED], "resolved")
        self.assertEqual(STATUS_TO_SEVERITY[CheckStatus.SKIPPED], "info")
```

In the `push_to_hub` command test file, a test that a `CheckResult` with
`CheckStatus.SKIPPED` produces `status == "resolved"` and `severity == "info"` from
`Command()._result_to_alert(result, "n1", "h1")`.

In `config/_tests/` next to the existing badge tests, assert the check-status table maps
`"skipped"` to `"muted"`.

Run: `uv run pytest apps/alerts/_tests/test_check_integration.py -k Skipped -q`
Expected: FAIL with `AttributeError: SKIPPED`.

**Step 2: Implement**

`apps/checkers/checkers/base.py`:

```python
    SKIPPED = "skipped"  # Not applicable on this machine; never raises an alert
```

`apps/checkers/models.py`:

```python
    SKIPPED = "skipped", "Skipped"
```

Run: `uv run python manage.py makemigrations checkers -n checkrun_status_skipped`

Add `CheckStatus.SKIPPED: AlertSeverity.INFO` and `CheckStatus.SKIPPED: "resolved"` to
the two tables in `check_integration.py`. In `push_to_hub._result_to_alert`, change the
first branch to `if result.status in (CheckStatus.OK, CheckStatus.SKIPPED):`. Add
`"skipped": "muted"` to the check-status tone table in `config/admin_badges.py`.

**Step 3: Run to verify pass**

Run: `uv run pytest apps/alerts apps/checkers config -q`
Expected: all pass.

**Step 4: Commit**

```bash
git add apps/checkers apps/alerts config/admin_badges.py config/_tests
git commit -m "feat(checkers): add a skipped status that resolves like ok and never fires"
```

---

### Task 6: Skips report `skipped`

**Files:**
- Modify: `apps/checkers/checkers/cpu_temp.py:43`, `disk_temp.py`, `io_strain.py`, `listening_ports.py`, `reboot_debian.py`, `raid.py` (each `_skip`), `disk_inodes.py:32`, `disk/base.py:47`
- Test: each checker's file under `apps/checkers/_tests/checkers/`

**Step 1: Update the tests first**

Find every assertion on a skip:

Run: `grep -rn "Skipped" apps/checkers/_tests`

For each test that checks a skip message, also assert
`self.assertEqual(result.status, CheckStatus.SKIPPED)`. Where a test asserts
`CheckStatus.OK` for a skip, change it to `SKIPPED`.

Run: `uv run pytest apps/checkers -q`
Expected: those tests FAIL.

**Step 2: Implement**

In each of the eight places, change `status=CheckStatus.OK` to
`status=CheckStatus.SKIPPED`. Keep the `Skipped: <reason>` messages. PR 2's describers
replace them.

**Step 3: Run to verify pass**

Run: `uv run pytest apps/checkers -q`
Expected: all pass.

**Step 4: Commit**

```bash
git add apps/checkers
git commit -m "feat(checkers): report not-applicable checks as skipped, not ok"
```

---

### Task 7: CLI output counts skips

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py:245-307`
- Modify: `apps/checkers/management/commands/run_check.py:150`
- Test: `apps/checkers/_tests/commands/` (check_health and run_check tests)

**Step 1: Write the failing tests**

- `check_health` with one OK and one SKIPPED result prints `Skipped: 1` in the summary
  line, and `--json` output has `"skipped": 1`.
- `_determine_exit_code` returns 0 for a list holding only SKIPPED results, with and
  without `--fail-on-warning`.
- `run_check` styles a SKIPPED result with the muted style used for OK. Assert on the
  `[SKIPPED]` text.

Run: `uv run pytest apps/checkers/_tests/commands -q`
Expected: the new tests FAIL.

**Step 2: Implement**

In `check_health.py`, render `SKIPPED` with the same style as `OK` in the per-result
branch, add `skip_count = sum(1 for r in results if r.status == CheckStatus.SKIPPED)` and
`Skipped: {skip_count}` to the summary, and `"skipped"` to the JSON counts. The exit code
needs no change, since it only looks for critical, warning and unknown. Make `run_check.py`'s
`OK` branch `in (CheckStatus.OK, CheckStatus.SKIPPED)`.

**Step 3: Run to verify pass**

Run: `uv run pytest apps/checkers -q`
Expected: all pass.

**Step 4: Commit**

```bash
git add apps/checkers
git commit -m "feat(checkers): count skipped checks in check_health and run_check output"
```

---

### Task 8: Docs

**Files:**
- Modify: `apps/alerts/AGENTS.md` (the "alert name is deliberately stable" bullet)
- Modify: `AGENTS.md` ("Checkers as an alert producer", the sentence ending "with a stable alert name")
- Modify: `apps/checkers/AGENTS.md` (status list, if it enumerates statuses)

**Step 1: Rewrite the alerts bullet**

Replace the "alert name is deliberately stable" bullet in `apps/alerts/AGENTS.md` with:

```markdown
- **Incidents group on `(subject, instance)`, never on display text.** `Alert.subject` is
  `checker:<name>` for checker alerts and `<source>:<name>` otherwise
  (`apps.alerts.identity.alert_subject`), written on every alert write. An incident stamps
  `subject`, `instance` and `node` when it is created, and `_find_open_incident` matches
  the first two. `Alert.name` is display text only.
```

**Step 2: Root AGENTS.md**

Change "with a stable alert name" to "and incidents group on `(subject, instance)`".

**Step 3: Checkers AGENTS.md**

Where statuses are listed, add `skipped`: not applicable on this machine, resolves like
`ok`, never fires.

**Step 4: Commit**

```bash
git add AGENTS.md apps/alerts/AGENTS.md apps/checkers/AGENTS.md
git commit -m "docs: incidents group on subject and instance; checks can be skipped"
```

---

### Task 9: Verify

**Step 1: Full suite and coverage**

Run: `uv run coverage run -m pytest -q && uv run coverage report --skip-covered | tail -20`
Expected: all pass. No changed line in `apps/alerts/identity.py`, `apps/alerts/services.py`,
`0012_subject_keys.py`, `check_integration.py`, `push_to_hub.py`, `check_health.py` or
`run_check.py` is missing.

**Step 2: Lint, format, checks**

Run: `uv run black . --check && uv run ruff check . && uv run python manage.py check && uv run python manage.py makemigrations --check --dry-run`
Expected: clean, `No changes detected`.

**Step 3: Smoke against the local DB**

Run: `uv run python manage.py migrate && uv run python manage.py check_health --no-notify`
Expected: the summary line shows `Skipped: N` on macOS. In a shell,
`Incident.objects.exclude(subject="").count()` equals the incident count, and no new
incident appeared for a condition that already had an open one.
