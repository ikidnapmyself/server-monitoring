---
title: "Hub Self-Monitoring: The Hub Is a Node"
parent: Plans
---

# Hub Self-Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make a hub monitor itself through the same alert, incident, and orchestration path it already uses for its agents, by registering itself in the `Node` registry and keying checker alerts on `instance_id`.

**Architecture:** `CheckAlertBridge` already converts `CheckResult` into `ParsedAlert` and writes it through `AlertOrchestrator` without touching the inbox. This plan makes that bridge the single translator for every checker-origin alert, gives it a stable identity keyed on `instance_id` instead of hostname, and adds two delivery modes on top of it. `push_to_hub --local` records a `PENDING` run so local checks drain exactly like a remote agent push. `check_health` keeps its current synchronous behaviour and writes alerts inline, so a single-machine install works with no cron, no hub, and no inbox processor.

**Tech Stack:** Django 5, pytest + pytest-django, uv.

---

## Decisions already made

1. **Fingerprint format is `check:{instance_id}:{checker_name}`.** Hostname is not identity. Two stock MacBooks both report `MacBook-Pro.local` and would share one Alert row. `instance_id` is the `Node` primary key and survives renames.
2. **One source name for checker-origin alerts.** Alert identity is `(fingerprint, source)`, verified at `apps/alerts/services.py:269`. Two source names means two rows for one condition, so `CheckAlertBridge.SOURCE_NAME` moves from `server-checkers` to `cluster`. Under "the hub is a node" that name is honest for both transports.
3. **Alert name must be stable.** `_find_open_incident` groups on `alert.name` (`apps/alerts/services.py:521`). The cluster path builds `f"{checker}: {message}"`, and the message carries live metrics, so the name drifts every tick and grouping silently splits. Both paths move to the bridge's stable `f"{CHECKER} Check Alert"`.
4. **`check_health` writes alerts by default, `--no-alert` opts out.** This follows the `preflight` precedent, which persists by default with `--no-save` for CI.
5. **`hub-self-check` is retired.** Decided 2026-08-28. The hub's own checks route through `cluster-nodes` like any other node's, so the hub can page about itself. See Task 7.
6. **The self node registers from both local paths.** A machine that produces truth about itself belongs in the registry the moment it does so.

## Known consequences, accepted

- Slack and email headlines for node alerts change from `cpu: CPU usage 91%` to `CPU Check Alert`. The message survives in the description.
- A machine running both `check_health` and `push_to_hub --local` produces one alert row, not two, because decisions 1 and 2 give both paths the same identity.
- On a standalone install with nothing scheduled, the readiness panel shows the self node as stale after `NODE_RECENT_MINUTES` (15). Task 9 addresses the wording only.

---

## Task 1: Shared identity helpers

**Files:**
- Create: `apps/alerts/identity.py`
- Test: `apps/alerts/_tests/test_identity.py`

**Step 1: Write the failing test**

```python
"""Tests for checker alert identity helpers."""

from django.test import SimpleTestCase, override_settings

from apps.alerts.identity import checker_fingerprint, local_instance_id


class CheckerFingerprintTests(SimpleTestCase):
    def test_format_is_readable_and_prefixed(self):
        self.assertEqual(checker_fingerprint("web-01-a3f2", "cpu"), "check:web-01-a3f2:cpu")

    def test_instance_id_separates_same_checker_on_two_machines(self):
        self.assertNotEqual(
            checker_fingerprint("a", "cpu"),
            checker_fingerprint("b", "cpu"),
        )

    def test_underscored_checker_names_survive(self):
        self.assertEqual(checker_fingerprint("n1", "disk_macos"), "check:n1:disk_macos")


class LocalInstanceIdTests(SimpleTestCase):
    @override_settings(INSTANCE_ID="configured-id")
    def test_prefers_configured_instance_id(self):
        self.assertEqual(local_instance_id(), "configured-id")

    @override_settings(INSTANCE_ID="")
    def test_falls_back_to_hostname(self):
        self.assertEqual(local_instance_id(), socket.gethostname())
```

Add `import socket` at the top of the test file.

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_identity.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'apps.alerts.identity'`

**Step 3: Write minimal implementation**

```python
"""Identity of a checker-origin alert: which machine, which checker.

Both local producers (``check_health`` writing inline, ``push_to_hub``
serialising for a hub) and the node-side push use these, so one condition on
one machine is one Alert row no matter how it arrived.
"""

import socket

from django.conf import settings


def local_instance_id() -> str:
    """This machine's registry key. Falls back to hostname when unconfigured."""
    return getattr(settings, "INSTANCE_ID", "") or socket.gethostname()


def checker_fingerprint(instance_id: str, checker_name: str) -> str:
    """Stable dedup key for a checker result on one machine.

    Keyed on ``instance_id`` rather than hostname: hostnames collide across
    stock installs and change on rename, while the instance id is the Node
    primary key.
    """
    return f"check:{instance_id}:{checker_name}"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_identity.py -v`
Expected: PASS, 5 tests

**Step 5: Commit**

```bash
git add apps/alerts/identity.py apps/alerts/_tests/test_identity.py
git commit -m "feat(alerts): instance-keyed identity helpers for checker alerts"
```

---

## Task 2: The bridge adopts the shared identity

**Files:**
- Modify: `apps/alerts/check_integration.py:94` (`SOURCE_NAME`), `:137-190` (label build, fingerprint, name)
- Test: `apps/alerts/_tests/test_check_integration.py`

**Step 1: Write the failing test**

```python
class BridgeIdentityTests(TestCase):
    @override_settings(INSTANCE_ID="node-a")
    def test_fingerprint_is_instance_keyed(self):
        bridge = CheckAlertBridge(hostname="host-a")
        parsed = bridge.check_result_to_parsed_alert(_ok_result("cpu"))
        self.assertEqual(parsed.fingerprint, "check:node-a:cpu")

    @override_settings(INSTANCE_ID="node-a")
    def test_instance_id_label_present_so_node_resolves(self):
        bridge = CheckAlertBridge(hostname="host-a")
        parsed = bridge.check_result_to_parsed_alert(_ok_result("cpu"))
        self.assertEqual(parsed.labels["instance_id"], "node-a")

    def test_name_is_stable_across_message_changes(self):
        bridge = CheckAlertBridge(hostname="host-a")
        first = bridge.check_result_to_parsed_alert(_result("cpu", "CPU usage 91%"))
        second = bridge.check_result_to_parsed_alert(_result("cpu", "CPU usage 94%"))
        self.assertEqual(first.name, second.name)

    def test_source_name_is_cluster(self):
        self.assertEqual(CheckAlertBridge.SOURCE_NAME, "cluster")
```

Write `_ok_result` and `_result` helpers building a `CheckResult` if the test module has none.

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_check_integration.py -k Identity -v`
Expected: FAIL on the sha256 fingerprint and the missing `instance_id` label.

**Step 3: Implement**

In `CheckAlertBridge`:

- Set `SOURCE_NAME = "cluster"`.
- Add `instance_id` to `__init__`, defaulting to `local_instance_id()`, stored on `self.instance_id`.
- In `check_result_to_parsed_alert`, add `"instance_id": self.instance_id` to `alert_labels`.
- Replace the fingerprint line with `fingerprint = checker_fingerprint(self.instance_id, result.checker_name)`.
- Delete `_generate_fingerprint` and the now-unused `hashlib` import.

Leave the `name` build alone. It is already `f"{result.checker_name.upper()} Check Alert"`, which is the stable form.

**Step 4: Run the app suite**

Run: `uv run pytest apps/alerts -q`
Expected: PASS. Fix any test asserting the old sha256 fingerprint or `server-checkers` source.

**Step 5: Commit**

```bash
git add apps/alerts/check_integration.py apps/alerts/_tests/test_check_integration.py
git commit -m "refactor(alerts): bridge keys checker alerts on instance_id"
```

---

## Task 3: The bridge registers the machine it is checking

**Files:**
- Modify: `apps/alerts/check_integration.py` (`_process_parsed_payload`)
- Test: `apps/alerts/_tests/test_check_integration.py`

**Step 1: Write the failing test**

```python
class BridgeSelfRegistrationTests(TestCase):
    @override_settings(INSTANCE_ID="hub-1")
    def test_local_run_registers_this_machine_as_a_node(self):
        bridge = CheckAlertBridge(hostname="hub-host")
        bridge.process_check_result(_result("cpu", "CPU usage 91%", CheckStatus.CRITICAL))
        node = Node.objects.get(instance_id="hub-1")
        self.assertEqual(node.hostname, "hub-host")
        self.assertEqual(node.last_source, "local")

    @override_settings(INSTANCE_ID="hub-1")
    def test_alert_links_to_the_registered_node(self):
        bridge = CheckAlertBridge(hostname="hub-host")
        bridge.process_check_result(_result("cpu", "CPU usage 91%", CheckStatus.CRITICAL))
        alert = Alert.objects.get(fingerprint="check:hub-1:cpu")
        self.assertEqual(alert.node.instance_id, "hub-1")
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_check_integration.py -k SelfRegistration -v`
Expected: FAIL, `Node.DoesNotExist`

**Step 3: Implement**

At the top of `_process_parsed_payload`, before the alert loop, inside the transaction:

```python
# The machine we just checked belongs in the registry the moment it produces
# truth about itself. Node.upsert is keyed on instance_id, so this is the same
# row a cluster push would create if this machine pushed to a hub.
Node.upsert(
    instance_id=self.instance_id,
    hostname=self.hostname,
    source="local",
)
```

Import `Node` from `apps.alerts.models` at module level. Registration must precede alert creation, because `_create_alert` calls `resolve_node(parsed.labels)` and only links to an already-registered node.

**Step 4: Run**

Run: `uv run pytest apps/alerts -q`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/check_integration.py apps/alerts/_tests/test_check_integration.py
git commit -m "feat(alerts): local checker runs register their own node"
```

---

## Task 4: The node-side push uses the same identity

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py:275-310` (`_result_to_alert`)
- Test: `apps/alerts/_tests/management/commands/test_push_to_hub.py`

**Step 1: Write the failing test**

```python
@override_settings(INSTANCE_ID="node-a")
def test_pushed_alert_shares_the_bridge_fingerprint(self):
    cmd = Command()
    alert = cmd._result_to_alert(_ok_result("cpu"), "node-a", "host-a")
    self.assertEqual(alert["fingerprint"], "check:node-a:cpu")

@override_settings(INSTANCE_ID="node-a")
def test_pushed_alert_name_matches_the_bridge_name(self):
    cmd = Command()
    alert = cmd._result_to_alert(_ok_result("cpu"), "node-a", "host-a")
    self.assertEqual(alert["name"], "CPU Check Alert")
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/management/commands/test_push_to_hub.py -k fingerprint -v`
Expected: FAIL, got `cpu-host-a`

**Step 3: Implement**

In `_result_to_alert`, replace the fingerprint and name lines:

```python
"fingerprint": checker_fingerprint(instance_id, result.checker_name),
"name": f"{result.checker_name.upper()} Check Alert",
```

Import `checker_fingerprint` from `apps.alerts.identity`. Keep the description as `result.message`, which is where the live text belongs.

**Step 4: Run**

Run: `uv run pytest apps/alerts -q`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/management/commands/test_push_to_hub.py
git commit -m "refactor(alerts): node push shares the checker alert identity"
```

---

## Task 5: Migrate existing checker alerts

**Files:**
- Create: `apps/alerts/migrations/00XX_checker_alert_identity.py`
- Test: `apps/alerts/_tests/test_identity_migration.py`

Two legacy formats exist. Cluster pushes wrote `f"{checker}-{hostname}"`. The bridge wrote `sha256(f"{checker}:{hostname}")[:16]`. Both carry a `checker` label, and cluster alerts carry `instance_id`. That is enough to recompute.

**Step 1: Write the failing test**

Use `django_test_migrations` if it is already a dependency. If it is not, test the pure helper instead and keep the migration a thin caller. Prefer the second, it adds no dependency.

```python
class MigrationHelperTests(SimpleTestCase):
    def test_uses_instance_id_label_when_present(self):
        new = new_fingerprint_for({"checker": "cpu", "instance_id": "n1", "hostname": "h1"}, "fallback")
        self.assertEqual(new, "check:n1:cpu")

    def test_falls_back_to_hostname_label(self):
        new = new_fingerprint_for({"checker": "cpu", "hostname": "h1"}, "fallback")
        self.assertEqual(new, "check:h1:cpu")

    def test_falls_back_to_local_instance_when_no_labels(self):
        new = new_fingerprint_for({"checker": "cpu"}, "fallback")
        self.assertEqual(new, "check:fallback:cpu")

    def test_returns_none_without_a_checker_label(self):
        self.assertIsNone(new_fingerprint_for({"hostname": "h1"}, "fallback"))
```

Put `new_fingerprint_for` in `apps/alerts/identity.py`.

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_identity_migration.py -v`
Expected: FAIL, `ImportError`

**Step 3: Implement the helper**

```python
def new_fingerprint_for(labels: dict | None, fallback_instance_id: str) -> str | None:
    """Recompute a legacy checker alert's fingerprint from its labels.

    None when the alert is not checker-origin (no ``checker`` label), which is
    how the migration skips webhook alerts.
    """
    labels = labels or {}
    checker = labels.get("checker")
    if not checker:
        return None
    instance = labels.get("instance_id") or labels.get("hostname") or fallback_instance_id
    return checker_fingerprint(instance, checker)
```

**Step 4: Write the migration**

```python
def forward(apps, schema_editor):
    Alert = apps.get_model("alerts", "Alert")
    seen: dict[tuple[str, str], int] = {}
    qs = Alert.objects.filter(source__in=["cluster", "server-checkers"]).order_by("-received_at")
    for alert in qs.iterator():
        new = new_fingerprint_for(alert.labels, local_instance_id())
        if new is None:
            continue
        key = (new, "cluster")
        if key in seen:
            # Two legacy rows collapse onto one identity. The newest keeps it;
            # older ones are parked under a legacy key so nothing silently
            # merges two histories into one alert.
            alert.fingerprint = f"{new}:legacy:{alert.pk}"
        else:
            seen[key] = alert.pk
            alert.fingerprint = new
        alert.source = "cluster"
        alert.save(update_fields=["fingerprint", "source"])
```

Add a no-op `reverse` so the migration is reversible in form. Recomputing the old sha256 is not possible for rows whose hostname label is gone, so document that a real rollback is a database restore.

**Step 5: Run the full suite and check migrations apply**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run pytest apps/alerts -q
```
Expected: no pending model changes, tests pass.

**Step 6: Commit**

```bash
git add apps/alerts/identity.py apps/alerts/migrations apps/alerts/_tests/test_identity_migration.py
git commit -m "feat(alerts): migrate checker alerts to instance-keyed identity"
```

---

## Task 6: `check_health` writes alerts inline

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py` (arguments, `handle`)
- Test: `apps/checkers/_tests/management/commands/test_check_health.py`

The command keeps its output, its exit codes, and its argument set. It gains alert writing that cannot change either.

**Step 1: Write the failing test**

```python
@override_settings(INSTANCE_ID="solo-mac")
class CheckHealthAlertTests(TestCase):
    def test_writes_an_alert_for_a_firing_checker(self):
        call_command("check_health", "cpu")
        self.assertTrue(Alert.objects.filter(fingerprint="check:solo-mac:cpu").exists())

    def test_no_alert_flag_writes_nothing(self):
        call_command("check_health", "cpu", "--no-alert")
        self.assertFalse(Alert.objects.exists())

    def test_enqueues_no_pipeline_run(self):
        call_command("check_health", "cpu")
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_alert_write_failure_does_not_change_exit_code(self):
        with patch.object(CheckAlertBridge, "run_checks_and_alert", side_effect=OperationalError("no such table")):
            call_command("check_health", "cpu")  # must not raise
```

Mock the checker so `cpu` returns CRITICAL deterministically, following the mocking style already used in that test module.

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/checkers/_tests/management/commands/test_check_health.py -k Alert -v`
Expected: FAIL, no Alert rows

**Step 3: Implement**

Add the argument:

```python
parser.add_argument(
    "--no-alert",
    action="store_true",
    help="Run checks without recording alerts (print only).",
)
```

In `handle`, after `results` is built and before output, call a new private method:

```python
if not options["no_alert"]:
    self._record_alerts(results)
```

```python
def _record_alerts(self, results) -> None:
    """Write alerts for these results on this machine. Never affects output.

    Synchronous and inbox-free by construction: CheckAlertBridge writes alerts
    and incidents in one transaction and enqueues nothing. That is what makes a
    single-machine install work with no hub, no cron, and no drain. A database
    that is missing or unmigrated must not break a health check, so failures are
    reported on stderr and swallowed.
    """
    from apps.alerts.check_integration import CheckAlertBridge

    try:
        CheckAlertBridge().process_check_results(results)
    except Exception as exc:  # noqa: BLE001 - a health check must still print
        self.stderr.write(f"Alert recording skipped: {exc}")
```

If `CheckAlertBridge` has no `process_check_results` taking already-computed results, add one. It loops `process_check_result`, which avoids re-running every checker. Do not call `run_checks_and_alert` here, the checks have already run.

**Step 4: Run**

```bash
uv run pytest apps/checkers apps/alerts -q
```
Expected: PASS

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/check_health.py apps/checkers/_tests apps/alerts/check_integration.py
git commit -m "feat(checkers): check_health records alerts locally without the inbox"
```

---

## Task 7: Retire the `hub-self-check` lane

**Files:**
- Modify: `apps/orchestration/seeding.py`
- Create: `apps/orchestration/migrations/00XX_retire_hub_self_check.py`
- Test: `apps/orchestration/_tests/` (seeding and routing tests)

**Why.** A lane named `hub-self-check` already exists at `apps/orchestration/seeding.py:65`. It matches `origin is checker_generated`, carries `stages: []`, and is record-only. `cluster-nodes` matches `source is cluster` with `["analyze", "notify"]`. Both sit at priority 50, and `resolve_pipeline` breaks ties by `id` (`apps/orchestration/routing.py:82`), so after the Task 2 source rename a hub-local run matches both and silently takes whichever was seeded first.

The lane's stated reason is that a five-minute cron re-reports a still-firing alert every tick. The incident change gate closed that: a repeat of an unchanged alert is absorbed and enqueues nothing. And a hub that never pages about its own full disk is not monitoring itself. The lane's premise, that the hub's own checks are a special case, is exactly what this branch abolishes.

**Consequence, accepted:** `run_pipeline --checks-only` sets the same `checker_generated` origin (`apps/orchestration/management/commands/run_pipeline.py:144`), so it starts analyzing and notifying too. That is the only other producer of that origin.

**Step 1: Write the failing test**

```python
def test_hub_local_run_routes_to_the_node_lane(self):
    seed_routing_table(PipelineDefinition, NotificationChannel)
    facts = {"source": "cluster", "origin": "checker_generated", ...}
    self.assertEqual(resolve_pipeline(facts).name, "cluster-nodes")

def test_hub_self_check_lane_is_not_seeded(self):
    seed_routing_table(PipelineDefinition, NotificationChannel)
    self.assertFalse(PipelineDefinition.objects.filter(name="hub-self-check").exists())
```

**Step 2: Run, confirm it resolves to `hub-self-check` today.**

**Step 3: Implement**

- Remove the `hub-self-check` entry from the seed list and from `_PRIOR_STAGES` in `seeding.py`.
- Add a data migration that DEACTIVATES the existing row (`is_active=False`), never deletes it: `Incident.pipeline` is a `SET_NULL` FK (`apps/alerts/models.py:240`), so deleting would blank which lane handled every past incident. Deactivate only when the row still carries the shape the earlier migrations seeded (`match` on `origin is checker_generated`, empty `stages`, priority 50). Reuse the module's existing principle, stated at `seeding.py:85`: a row still carrying exactly the seeded shape has never been edited, so reshaping it is repair rather than overwriting an operator's decision. An edited row is left alone and the migration logs that it was kept.
- Update the orchestration routing fixtures that pass `source="server-checkers"` as a literal so they exercise the real `source="cluster"` pairing.

**Step 4: Run** `uv run pytest apps/orchestration apps/alerts -q`, then the full suite.

**Step 5: Commit**

```bash
git commit -m "feat(orchestration): the hub's own checks route like any node"
```

---

## Task 8: `push_to_hub --local`

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py` (arguments, `handle`)
- Test: `apps/alerts/_tests/management/commands/test_push_to_hub.py`

`--local` skips HTTP and records the same payload as a `PENDING` run, so it drains through `IngestExecutor` and `ClusterDriver` exactly like a remote agent.

**Step 1: Write the failing test**

```python
@override_settings(INSTANCE_ID="hub-1", HUB_URL="")
class PushToHubLocalTests(TestCase):
    def test_local_records_a_pending_run(self):
        call_command("push_to_hub", "--local", "--checkers", "cpu")
        run = PipelineRun.objects.get()
        self.assertEqual(run.status, PipelineStatus.PENDING)
        self.assertEqual(run.origin, PipelineOrigin.CHECKER_GENERATED)
        self.assertEqual(run.inbound_payload["driver"], "cluster")

    def test_local_registers_this_machine(self):
        call_command("push_to_hub", "--local", "--checkers", "cpu")
        self.assertTrue(Node.objects.filter(instance_id="hub-1").exists())

    def test_local_needs_no_hub_url(self):
        call_command("push_to_hub", "--local", "--checkers", "cpu")  # must not raise

    def test_local_sends_no_http(self):
        with patch("apps.alerts.management.commands.push_to_hub.send_to_hub") as sender:
            call_command("push_to_hub", "--local", "--checkers", "cpu")
        sender.assert_not_called()
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/management/commands/test_push_to_hub.py -k Local -v`
Expected: FAIL, unrecognised argument `--local`

**Step 3: Implement**

Add the argument:

```python
parser.add_argument(
    "--local",
    action="store_true",
    help="Record results on this instance's inbox instead of POSTing to a hub.",
)
```

In `handle`, move the `HUB_URL` requirement behind the flag. `--local` needs no hub. After the payload is built:

```python
if options["local"]:
    # Same shape a remote agent produces, minus the network. Recording a
    # PENDING run rather than writing alerts directly is what keeps a local
    # push identical to a peer's: one drain path, one set of executors, one
    # routing decision.
    from apps.orchestration.models import PipelineOrigin
    from apps.orchestration.orchestrator import PipelineOrchestrator

    from apps.alerts.services import register_pushing_node

    register_pushing_node(payload)
    run = PipelineOrchestrator().start_pipeline(
        payload={"driver": "cluster", "payload": payload},
        source="cluster",
        origin=PipelineOrigin.CHECKER_GENERATED,
    )
    self.stdout.write(f"Recorded local run {run.run_id} ({len(alerts)} alerts)")
    return
```

`register_pushing_node` sets `last_source="cluster"`. That is correct here, the payload is a cluster payload.

**Step 4: Run**

Run: `uv run pytest apps/alerts -q`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/management/commands/test_push_to_hub.py
git commit -m "feat(alerts): push_to_hub --local records a run instead of POSTing"
```

---

## Task 9: Installation sets a collision-resistant `INSTANCE_ID` for every role

**Files:**
- Modify: `bin/install/cluster.sh:71-81`
- Modify: `bin/install/cron.sh:118-144`
- Modify: `config/management/commands/setup_cluster.py:300-315`
- Test: `bin/tests/` following the existing bats layout

`INSTANCE_ID` is prompted only on the agent path today and defaults to `hostname`, which is exactly the collision the fingerprint change removes. Set it for every role, and default it to hostname plus a short random suffix.

**Step 1: Write the failing bats test**

Assert that a hub-only install writes a non-empty `INSTANCE_ID` to `.env`, and that the default is not bare `hostname`.

**Step 2: Run it, confirm it fails**

Run: `bin/tests/run.sh` or the project's usual bats entry point.

**Step 3: Implement**

- Move the `INSTANCE_ID` prompt out of the agent-only branch so every role gets one.
- Default to `"$(hostname)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"`.
- Replace the "Schedule automatic push to hub?" prompt for a hub pointing at itself with a "Schedule local self-checks?" prompt that installs `push_to_hub --local`, which needs no `HUB_URL`.
- Mirror the same default in `setup_cluster.py`.

**Step 4: Run the shell tests and the Python suite**

**Step 5: Commit**

```bash
git add bin config/management/commands/setup_cluster.py bin/tests
git commit -m "feat(install): every role gets a collision-resistant INSTANCE_ID"
```

---

## Task 10: Docs

**Files:**
- Modify: `apps/alerts/AGENTS.md`, `apps/checkers/AGENTS.md`, `apps/checkers/README.md`, `AGENTS.md`, `docs/Architecture.md`
- Modify: `docs/Deployment.md` (lines ~507, ~533), `docs/Setup-Guide.md` (~231), `docs/Installation.md` (~334) — all still describe the retired `hub-self-check` lane as live routing behaviour
- Modify: `apps/alerts/models.py:356` (the `Node` docstring)

Cover:

- `Node` now holds this instance too. Update the docstring, which currently says "a peer that has pushed cluster data to this instance".
- The fingerprint format and why it is keyed on `instance_id`.
- `check_health` records alerts by default and never touches the inbox. `--no-alert` for CI.
- `push_to_hub --local` as the scheduled self-monitoring path.
- The readiness panel counts the self node, so a machine with nothing scheduled reads as stale after 15 minutes.

**Commit**

```bash
git add -A
git commit -m "docs: hub self-monitoring, instance-keyed checker alerts"
```

---

## Verification before the branch is done

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run python manage.py makemigrations --check --dry-run
uv run bandit -r apps/ config/ -c pyproject.toml
```

Manual pass on a scratch database:

```bash
uv run python manage.py migrate
uv run python manage.py check_health cpu            # writes an alert, no run
uv run python manage.py push_to_hub --local         # writes a PENDING run
uv run python manage.py process_inbox               # drains it
uv run python manage.py trace <trace_id>            # one story end to end
```

Confirm in the admin that one `Node` row exists for this machine, that `cpu` produced one `Alert` and not two, and that the incident it belongs to is the same one both paths reached.

## Out of scope, worth a follow-up ticket

- `_check_incident_resolution` scans every open incident on every bridge run. Fine at eight nodes, not at eighty.
- The readiness panel has no way to say "this machine has no schedule, and that is intentional".
- `checker_fingerprint` does not validate `instance_id`. On the hub side that value arrives off the wire in a cluster payload, so a blank or attacker-chosen id becomes a fingerprint. Ingest should reject blank ids rather than key on them. Related to the deferred node-bound-key work.
- Preflight stays node-local, as decided.
