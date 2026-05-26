---
title: "2026-05-25 Observability Cluster Topology — Implementation"
parent: Plans
---
# Observability Cluster Topology Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The work is staged as **three sequential PRs**; this document covers PR 1 in detail and outlines PR 2 + PR 3. Detailed plans for PR 2 and PR 3 will be written once PR 1 lands and we know what (if anything) the implementation surfaced that the design didn't anticipate.

**Goal:** Build the cluster log-aggregation layer described in `docs/plans/2026-05-25-observability-cluster-topology-design.md` — definition-driven, multi-destination, forwarding-capable, CLI-first.

**Architecture:** New `ClusterDestination` model is the single source of truth for "where this host pushes logs". The formatter stamps every record with a `record_id` (uuid) and an empty `path[]`; forwarders append to `path`, receivers dedup by `record_id` and break loops via `path`. Operators configure everything via `manage.py cluster_dest*` commands, wrapped by `bin/cli/cluster.sh` menus. Django admin gets the model for free (secondary surface).

**Tech Stack:** Django 5 + DRF (existing), `apps.observability` (existing), `apps.alerts.APIKey` (existing, gains one field), `bin/cli/cluster.sh` (existing bash menu).

**Working branch:** `feat/observability-cluster-topology` (already created off main; design doc committed in `029361a`).

---

## PR staging overview

The work splits cleanly along data-flow boundaries. Each PR is independently shippable and useful on its own.

| PR | Scope | What an operator can do after it lands | Approx LOC |
|---|---|---|---|
| **PR 1 — Foundation: model, contract, CLI** | `ClusterDestination` model + migration; `APIKey.owner_instance_id` field; formatter stamps `record_id` + empty `path[]`; 8 `cluster_dest*` management commands; `bin/cli/cluster.sh` extended with destination menu; admin registration. | Define and validate the topology end-to-end. Add/list/remove/test destinations. No logs are pushed yet — purely the configuration plane. | ~600 |
| **PR 2 — Push & receive: single-hop fan-in** | `push_logs_to_hub` command with per-destination cursors and local-only source; `POST /cluster/logs/<stream>/` view with auth + LRU dedup + cycle-back check + per-instance storage; per-destination heartbeats; `cluster_status` command; `O004` system check (per-destination freshness). | Real single-hop topologies work: star, fan-in with redundancy, hub-only. No multi-hop forwarding yet. | ~700 |
| **PR 3 — Forwarding: multi-hop & mesh** | `forward_received=True` semantics in push (read from `LOGS_DIR/cluster/<source>/` too); `path[]` appending before push; loop break via `path` ∩ `destination.api_key.owner_instance_id`; `O005` system check (hub-side instance freshness); `apps/observability/AGENTS.md` docs. | Full mesh / regional aggregator / multi-hop topologies. Loop prevention is tested for the four canonical scenarios. | ~500 |

**Total:** ~1800 LOC across three reviewable-in-a-day PRs vs ~1800 in one unreviewable mega-PR (the explicit anti-pattern that prompted this staging).

Each PR opens against `main` (not stacked on the previous PR's branch — keeps review independent). PR 2 starts after PR 1 merges; PR 3 starts after PR 2 merges.

---

## PR 1 — Foundation: model, contract, CLI

**Branch:** continue using `feat/observability-cluster-topology` (already checked out) for PR 1. PR 2 and PR 3 will use fresh branches off main.

**Note on TDD:** This work is mostly model + CLI + formatter changes — TDD applies cleanly. Each task ends with a failing test, minimal implementation, passing test, commit. The exception is the bash menu wiring (Task 1.10), which gets shell-script tests rather than pytest tests.

---

### Task 1.1: Add `APIKey.owner_instance_id` field

**Files:**
- Modify: `apps/alerts/models.py` (add field to APIKey)
- Create: `apps/alerts/migrations/00XX_apikey_owner_instance_id.py` (auto-generated)
- Test: `apps/alerts/_tests/test_models.py` (existing) — add test for default value

**Step 1: Write the failing test**

In `apps/alerts/_tests/test_models.py`, append:

```python
@pytest.mark.django_db
def test_apikey_owner_instance_id_defaults_to_name():
    from apps.alerts.models import APIKey
    key = APIKey.objects.create(name="agent-a", key_hash="x" * 64)
    assert key.owner_instance_id == "agent-a"


@pytest.mark.django_db
def test_apikey_owner_instance_id_overridable():
    from apps.alerts.models import APIKey
    key = APIKey.objects.create(
        name="agent-a", key_hash="x" * 64, owner_instance_id="custom-id"
    )
    assert key.owner_instance_id == "custom-id"
```

**Step 2: Run test, verify it fails**

`uv run pytest apps/alerts/_tests/test_models.py::test_apikey_owner_instance_id_defaults_to_name -v` — expected: AttributeError or similar; field does not exist.

**Step 3: Add the field**

In `apps/alerts/models.py`, on the `APIKey` class:

```python
owner_instance_id = models.CharField(
    max_length=64,
    blank=True,
    default="",
    help_text=(
        "Instance ID of the host this key was issued to. Used by cluster "
        "log forwarding's loop-prevention check. Defaults to `name` when blank."
    ),
)
```

Override `save()` to backfill the default:

```python
def save(self, *args, **kwargs):
    if not self.owner_instance_id:
        self.owner_instance_id = self.name
    super().save(*args, **kwargs)
```

**Step 4: Generate the migration**

`uv run python manage.py makemigrations alerts -n apikey_owner_instance_id` — expected: a new migration file created under `apps/alerts/migrations/`.

**Step 5: Run tests, verify pass**

`uv run pytest apps/alerts/_tests/test_models.py -v` — expected: all tests pass, including the two new ones.

**Step 6: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations/00XX_apikey_owner_instance_id.py apps/alerts/_tests/test_models.py
git commit -m "feat(alerts): APIKey.owner_instance_id for cluster loop-prevention"
```

---

### Task 1.2: Create `ClusterDestination` model

**Files:**
- Create: `apps/observability/models.py` (this app currently has no models)
- Create: `apps/observability/migrations/0001_clusterdestination.py` (auto-generated)
- Modify: `apps/observability/apps.py` (no change needed if `default_auto_field` already set at project level — verify)
- Test: `apps/observability/_tests/test_models.py` (new)

**Step 1: Write the failing test**

Create `apps/observability/_tests/test_models.py`:

```python
import pytest


@pytest.mark.django_db
def test_create_minimal_destination():
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination

    key = APIKey.objects.create(name="central-hub", key_hash="x" * 64)
    dest = ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
    )
    assert dest.streams == "events,heartbeats"          # default
    assert dest.forward_received is False                # default
    assert dest.is_active is True                        # default
    assert dest.max_batch_bytes == 10 * 1024 * 1024      # default
    assert dest.last_push_at is None
    assert dest.last_push_status == ""


@pytest.mark.django_db
def test_destination_name_is_unique():
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination
    from django.db import IntegrityError

    key = APIKey.objects.create(name="hub", key_hash="x" * 64)
    ClusterDestination.objects.create(name="dup", hub_url="https://a.example", api_key=key)
    with pytest.raises(IntegrityError):
        ClusterDestination.objects.create(name="dup", hub_url="https://b.example", api_key=key)


@pytest.mark.django_db
def test_destination_str_is_name():
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination

    key = APIKey.objects.create(name="hub", key_hash="x" * 64)
    dest = ClusterDestination.objects.create(name="central", hub_url="https://e.example", api_key=key)
    assert str(dest) == "central"
```

**Step 2: Run, verify fail**

`uv run pytest apps/observability/_tests/test_models.py -v` — expected: ImportError, model doesn't exist.

**Step 3: Create the model**

`apps/observability/models.py`:

```python
"""Cluster log-forwarding destination registry."""

from __future__ import annotations

from django.db import models


class ClusterDestination(models.Model):
    """One outbound log-push destination this host knows about.

    A host with zero rows is hub-only / standalone (no outbound push).
    A host with one or more rows pushes its local logs (and, if
    `forward_received=True`, records received from other agents) to each
    listed hub. Loop prevention is structural via the JSONL record's
    `path` field — see docs/plans/2026-05-25-observability-cluster-topology-design.md.
    """

    name = models.CharField(max_length=64, unique=True)
    hub_url = models.URLField()
    api_key = models.ForeignKey(
        "alerts.APIKey",
        on_delete=models.PROTECT,
        related_name="cluster_destinations",
    )
    streams = models.CharField(
        max_length=128,
        default="events,heartbeats",
        help_text="Comma-separated list of streams to push (e.g. 'events,heartbeats').",
    )
    forward_received = models.BooleanField(
        default=False,
        help_text=(
            "When true, this destination also re-pushes records this host "
            "received from other agents (subject to loop-prevention via the "
            "record's `path` field). Default false: most nodes push only "
            "their own locally-generated logs."
        ),
    )
    is_active = models.BooleanField(default=True)
    max_batch_bytes = models.PositiveIntegerField(default=10 * 1024 * 1024)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_push_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text='e.g. "ok", "fail:401", "fail:5xx".',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
```

**Step 4: Generate migration**

`uv run python manage.py makemigrations observability -n clusterdestination` — expected: a new `apps/observability/migrations/0001_clusterdestination.py`.

**Step 5: Run tests**

`uv run pytest apps/observability/_tests/test_models.py -v` — expected: 3 passed.

**Step 6: Commit**

```bash
git add apps/observability/models.py apps/observability/migrations/ apps/observability/_tests/test_models.py
git commit -m "feat(observability): ClusterDestination model + migration"
```

---

### Task 1.3: Register `ClusterDestination` in Django admin

**Files:**
- Create: `apps/observability/admin.py` (this app currently has no admin)
- Test: `apps/observability/_tests/test_admin.py` (new)

**Step 1: Write the failing test**

Create `apps/observability/_tests/test_admin.py`:

```python
import pytest
from django.contrib import admin


@pytest.mark.django_db
def test_clusterdestination_registered_in_admin():
    from apps.observability.models import ClusterDestination
    assert ClusterDestination in admin.site._registry


@pytest.mark.django_db
def test_admin_list_display_includes_status_fields():
    from apps.observability.models import ClusterDestination
    cfg = admin.site._registry[ClusterDestination]
    for field in ("name", "hub_url", "streams", "forward_received",
                  "is_active", "last_push_at", "last_push_status"):
        assert field in cfg.list_display, f"missing {field} from list_display"
```

**Step 2: Run, verify fail**

`uv run pytest apps/observability/_tests/test_admin.py -v` — expected: ImportError (admin.py doesn't exist) or KeyError.

**Step 3: Implement admin**

`apps/observability/admin.py`:

```python
"""Admin registration for observability models.

This is the secondary operations surface; the primary one is the CLI
(`bin/cli.sh cluster` → `manage.py cluster_dest*`). Admin is here so the
project rule that every app provides substantive admin holds, and so
operators can spot-check destination state in a familiar UI.
"""

from django.contrib import admin

from apps.observability.models import ClusterDestination


@admin.register(ClusterDestination)
class ClusterDestinationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "hub_url",
        "streams",
        "forward_received",
        "is_active",
        "last_push_at",
        "last_push_status",
    )
    list_filter = ("is_active", "forward_received")
    search_fields = ("name", "hub_url")
    readonly_fields = ("last_push_at", "last_push_status", "created_at", "updated_at")
```

**Step 4: Run tests**

`uv run pytest apps/observability/_tests/test_admin.py -v` — expected: 2 passed.

**Step 5: Commit**

```bash
git add apps/observability/admin.py apps/observability/_tests/test_admin.py
git commit -m "feat(observability): admin registration for ClusterDestination"
```

---

### Task 1.4: Formatter stamps `record_id` and empty `path[]`

**Files:**
- Modify: `apps/observability/formatter.py` (add fields to JSON output)
- Modify: `apps/observability/_tests/test_formatter.py` (add tests for new fields)

**Step 1: Write the failing tests**

In `apps/observability/_tests/test_formatter.py`, append:

```python
def test_record_has_record_id_uuid():
    import re
    import uuid

    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert "record_id" in obj
    # Verify it's a valid uuid4
    parsed = uuid.UUID(obj["record_id"])
    assert parsed.version == 4


def test_record_id_unique_per_emission():
    fmt = JsonLineFormatter()
    a = json.loads(fmt.format(make_record()))
    b = json.loads(fmt.format(make_record()))
    assert a["record_id"] != b["record_id"]


def test_record_has_empty_path_at_emit():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["path"] == []
```

**Step 2: Run, verify fail**

`uv run pytest apps/observability/_tests/test_formatter.py -v -k "record_id or path"` — expected: KeyError / AssertionError; fields not in output.

**Step 3: Add fields to formatter**

In `apps/observability/formatter.py`, find the `JsonLineFormatter.format()` method that builds the output dict. Add:

```python
import uuid  # at top of file if not already

# ... inside format(), after the existing field assembly ...
out["record_id"] = str(uuid.uuid4())
out["path"] = []
```

Place these **after** any per-record fields that might be overridden by `extra`, so `record_id` and `path` cannot be spoofed by a logger call. (Reviewer note: this is the spoofing-defence rationale; mention it in the commit message.)

**Step 4: Run tests**

`uv run pytest apps/observability/_tests/test_formatter.py -v` — expected: all pass (existing + new).

**Step 5: Commit**

```bash
git add apps/observability/formatter.py apps/observability/_tests/test_formatter.py
git commit -m "feat(observability): emit record_id (uuid4) and empty path[] on every record

These fields power cluster log forwarding: record_id enables LRU dedup
at receivers; path[] is appended to by each forwarder for loop break.
Both are stamped AFTER user-supplied extra fields so they cannot be
spoofed by a logger call carrying a forged record_id."
```

---

### Task 1.5: `cluster_dest add` management command

**Files:**
- Create: `apps/observability/management/commands/cluster_dest_add.py`
- Test: `apps/observability/_tests/management/test_cluster_dest_add.py`

**Step 1: Write the failing test**

Create `apps/observability/_tests/management/test_cluster_dest_add.py`:

```python
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_add_creates_destination(capsys):
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination

    APIKey.objects.create(name="hub-key", key_hash="x" * 64)
    call_command(
        "cluster_dest_add",
        "--name", "central",
        "--url", "https://central.example.com",
        "--api-key", "hub-key",
    )
    dest = ClusterDestination.objects.get(name="central")
    assert dest.hub_url == "https://central.example.com"
    assert dest.streams == "events,heartbeats"
    assert dest.forward_received is False


@pytest.mark.django_db
def test_add_duplicate_name_raises():
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination

    key = APIKey.objects.create(name="hub-key", key_hash="x" * 64)
    ClusterDestination.objects.create(name="central", hub_url="https://a.example", api_key=key)
    with pytest.raises(CommandError, match="already exists"):
        call_command(
            "cluster_dest_add",
            "--name", "central",
            "--url", "https://b.example.com",
            "--api-key", "hub-key",
        )


@pytest.mark.django_db
def test_add_unknown_api_key_raises():
    with pytest.raises(CommandError, match="No APIKey named"):
        call_command(
            "cluster_dest_add",
            "--name", "central",
            "--url", "https://central.example.com",
            "--api-key", "ghost-key",
        )


@pytest.mark.django_db
def test_add_with_forward_flag():
    from apps.alerts.models import APIKey
    from apps.observability.models import ClusterDestination

    APIKey.objects.create(name="hub-key", key_hash="x" * 64)
    call_command(
        "cluster_dest_add",
        "--name", "regional",
        "--url", "https://regional.example.com",
        "--api-key", "hub-key",
        "--forward",
    )
    dest = ClusterDestination.objects.get(name="regional")
    assert dest.forward_received is True
```

**Step 2: Run, verify fail**

`uv run pytest apps/observability/_tests/management/test_cluster_dest_add.py -v` — expected: CommandError "Unknown command: 'cluster_dest_add'".

**Step 3: Implement**

`apps/observability/management/commands/cluster_dest_add.py`:

```python
"""manage.py cluster_dest_add — register a new outbound log destination."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.alerts.models import APIKey
from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "Register a new cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Admin identifier (unique).")
        parser.add_argument("--url", required=True, help="Hub URL, e.g. https://hub.example.com")
        parser.add_argument(
            "--api-key",
            required=True,
            dest="api_key_name",
            help="Name of an existing APIKey to use as the auth credential.",
        )
        parser.add_argument(
            "--streams",
            default="events,heartbeats",
            help="Comma-separated streams to push (default: events,heartbeats).",
        )
        parser.add_argument(
            "--forward",
            action="store_true",
            dest="forward_received",
            help="Also re-push records received from other agents.",
        )
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        name = options["name"]
        if ClusterDestination.objects.filter(name=name).exists():
            raise CommandError(f"Destination '{name}' already exists.")
        try:
            api_key = APIKey.objects.get(name=options["api_key_name"])
        except APIKey.DoesNotExist:
            raise CommandError(f"No APIKey named '{options['api_key_name']}'.")

        dest = ClusterDestination.objects.create(
            name=name,
            hub_url=options["url"],
            api_key=api_key,
            streams=options["streams"],
            forward_received=options["forward_received"],
        )
        if options["json"]:
            import json
            self.stdout.write(json.dumps({
                "id": dest.id,
                "name": dest.name,
                "hub_url": dest.hub_url,
                "streams": dest.streams,
                "forward_received": dest.forward_received,
            }))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Created destination '{name}' → {dest.hub_url} (streams={dest.streams}, "
                f"forward_received={dest.forward_received})"
            ))
```

**Step 4: Run tests**

`uv run pytest apps/observability/_tests/management/test_cluster_dest_add.py -v` — expected: 4 passed.

**Step 5: Commit**

```bash
git add apps/observability/management/commands/cluster_dest_add.py apps/observability/_tests/management/test_cluster_dest_add.py
git commit -m "feat(observability): cluster_dest_add command"
```

---

### Task 1.6: `cluster_dest list` command

**Files:**
- Create: `apps/observability/management/commands/cluster_dest_list.py`
- Test: `apps/observability/_tests/management/test_cluster_dest_list.py`

Follow the same TDD pattern: write a test that calls `call_command("cluster_dest_list", "--json")` and asserts on the parsed output structure (a list of dicts with the documented fields). The text mode should print a table; the JSON mode should output an array.

Implementation: read all `ClusterDestination` rows, format. Sort by name.

Commit message: `feat(observability): cluster_dest_list command`.

---

### Task 1.7: `cluster_dest show <name>` command

**Files:**
- Create: `apps/observability/management/commands/cluster_dest_show.py`
- Test: `apps/observability/_tests/management/test_cluster_dest_show.py`

Tests:
- Show existing destination prints all fields.
- Show unknown destination raises `CommandError`.
- `--json` produces the documented structure.

PR 1 ships with the "recent push history" section left empty (no pushes happen until PR 2). The show command should display "No pushes yet" in that section for now.

Commit message: `feat(observability): cluster_dest_show command`.

---

### Task 1.8: `cluster_dest remove`, `toggle`, `forward` commands

Three near-identical commands. Each takes a `--name` and mutates one field on the destination row.

**Files (per command):**
- Create: `apps/observability/management/commands/cluster_dest_remove.py`
- Create: `apps/observability/management/commands/cluster_dest_toggle.py`
- Create: `apps/observability/management/commands/cluster_dest_forward.py`
- Tests: matching files under `apps/observability/_tests/management/`

**Semantics:**
- `remove --name X [--hard]` — default sets `is_active=False`; `--hard` deletes the row. Both raise `CommandError` if no such destination.
- `toggle --name X` — flips `is_active`. Prints the new state.
- `forward --name X {on|off}` — sets `forward_received` from the positional `on`/`off`. Other values → `CommandError`.

Each command keeps a test file with: success path, unknown-name path, the operation's specific edge case (`--hard` actually deletes; `toggle` round-trips back to original after two calls; `forward off` then `on` matches the second value).

Commit pattern: one commit per command. Three commits total.

---

### Task 1.9: `cluster_dest doctor <name>` command

**Files:**
- Create: `apps/observability/management/commands/cluster_dest_doctor.py`
- Test: `apps/observability/_tests/management/test_cluster_dest_doctor.py`

**Semantics:** one-shot diagnostic against a destination. Checks:

1. DNS resolves for `destination.hub_url`'s hostname.
2. TCP connect succeeds on the URL's port.
3. TLS handshake completes (if scheme is `https`).
4. `HEAD {hub_url}/cluster/logs/health/` returns a 200 with auth.

Each check prints `[✓]` / `[✗]` with reason. Exit 0 if all pass, exit 1 otherwise. JSON mode produces `{checks: [{name, ok, detail}, ...], summary: {ok: N, fail: M}}`.

**Networking is mocked in tests.** Use `unittest.mock.patch` against `socket.gethostbyname`, `socket.create_connection`, and `urllib.request.urlopen` (or whichever HTTP library the project uses for outbound calls — check `apps/alerts/management/commands/push_to_hub.py` for the existing convention).

**Project rule reminder:** any subprocess this command spawns must use the `shutil.which` full-path pattern (AGENTS.md rule #8). For this command there shouldn't be any subprocess — pure Python networking — but flag it if anything changes.

Commit message: `feat(observability): cluster_dest_doctor command`.

---

### Task 1.10: Extend `bin/cli/cluster.sh` with destination management menu

**Files:**
- Modify: `bin/cli/cluster.sh` (add menu entries)
- Test: `bin/tests/test_cluster_menu.bats` (new — follow the existing BATS test pattern under `bin/tests/`)

**Step 1: Inspect the existing menu structure**

Run: `cat bin/cli/cluster.sh` and identify the existing case statement that handles user input.

**Step 2: Add menu entries**

Extend the menu options:

```
Cluster menu
  1) Add destination
  2) List destinations
  3) Show destination details
  4) Remove destination
  5) Enable / disable destination
  6) Set forward-received policy
  7) Test destination
  8) Cluster status               (not implemented until PR 2)
  9) Push logs now (manual)       (not implemented until PR 2)
  10) Alerts: push to hub          (existing item — renumber)
  0) Back
```

For PR 1, items 8 and 9 print "Not yet implemented (lands in PR 2)" and return to the menu.

For interactive inputs, follow the existing `confirm_and_run` pattern used elsewhere in `bin/cli/`. For Add Destination specifically, prompt for each required field (name, URL, API key) with `read -rp`.

**Step 3: Write BATS tests**

Verify each menu choice calls the right `manage.py` command. Mock `uv run python manage.py` so the test doesn't actually invoke the command:

```bash
@test "menu choice 1 (Add destination) invokes cluster_dest_add" {
  run bash -c '...invoke cluster.sh with stdin "1\nfoo\nhttps://e.example\nkey\n\nn\n"...'
  [[ "$output" == *"cluster_dest_add"* ]]
}
```

(Concrete BATS plumbing follows the existing patterns in `bin/tests/`.)

**Step 4: Commit**

```bash
git add bin/cli/cluster.sh bin/tests/test_cluster_menu.bats
git commit -m "feat(cli): cluster destination management menu

Wraps the cluster_dest_* manage.py commands behind interactive prompts.
Items 8 and 9 (cluster status, push-now) are stubs that land in PR 2
of the cluster topology series."
```

---

### Task 1.11: Update `apps/observability/AGENTS.md` with cluster section

**Files:**
- Modify: `apps/observability/AGENTS.md` (add cluster section)

Add a section titled **"Cluster topology"** that:

1. Links to the design doc.
2. Documents the record contract additions (`record_id`, `instance_id`, `path[]`) and how the formatter sets them.
3. States the loop-prevention invariant (a host with `instance_id = X` will not forward to any destination whose `api_key.owner_instance_id = X`).
4. Lists the `cluster_dest*` commands with one-liners.
5. Notes that PR 1 ships configuration only; PR 2 ships push+receive; PR 3 ships forwarding.

Commit message: `docs(observability): cluster topology section in AGENTS.md`.

---

### Task 1.12: Verify, push, open PR 1

**Step 1: Full verification**

```bash
uv run pytest                                                            # all tests pass
uv run coverage run -m pytest && uv run coverage report --fail-under=100 # 100% branch on new code
uv run bandit -r apps/ config/ -c pyproject.toml                         # security clean
uv run pip-audit --strict --desc                                         # deps clean
uv run python manage.py check                                            # Django checks
uv run python manage.py migrate --plan                                    # migration plan sane
bash bin/tests/test_cluster_menu.bats                                    # bash tests pass
```

All expected: clean / green.

**Step 2: Push the branch**

```bash
git push -u origin feat/observability-cluster-topology
```

**Step 3: Open PR 1**

```bash
gh pr create --base main --head feat/observability-cluster-topology \
  --title "feat(observability): cluster topology — PR 1 of 3: foundation (model, contract, CLI)" \
  --body "$(cat <<'EOF'
## Summary

PR 1 of 3 implementing `docs/plans/2026-05-25-observability-cluster-topology-design.md`. This PR ships the **configuration plane** — operators can define and validate cluster destinations end-to-end via CLI; nothing is pushed yet. PR 2 adds push + receive; PR 3 adds forwarding.

## What's in this PR

- `ClusterDestination` model + migration.
- `APIKey.owner_instance_id` field (for cluster loop-prevention; defaults to `name`).
- `JsonLineFormatter` stamps `record_id` (uuid4) and empty `path[]` on every emitted record.
- 8 `manage.py cluster_dest*` commands: add, list, show, remove, toggle, forward, doctor.
- `bin/cli/cluster.sh` menu extended with interactive destination management.
- Admin registration with sensible list display.
- `apps/observability/AGENTS.md` cluster section.

## What's not in this PR (parked for PR 2 / PR 3)

- `push_logs_to_hub` (PR 2)
- `POST /cluster/logs/<stream>/` view + dedup (PR 2)
- `cluster_status` and `O004` system check (PR 2)
- `forward_received=True` semantics, multi-hop, loop prevention tests (PR 3)
- `O005` hub-side freshness check (PR 3)

## Test plan

- [x] `uv run pytest` — full suite green
- [x] `uv run coverage run -m pytest && uv run coverage report --fail-under=100` — 100% branch coverage on new code
- [x] `uv run bandit -r apps/ config/ -c pyproject.toml` — clean
- [x] `uv run pip-audit --strict --desc` — clean
- [x] `bash bin/tests/test_cluster_menu.bats` — bash tests pass
- [x] Manual: \`bin/cli.sh cluster\` → "Add destination" → fills prompts → row appears in admin

Refs: \`docs/plans/2026-05-25-observability-cluster-topology-design.md\`, \`docs/plans/2026-05-25-observability-cluster-topology-impl.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## PR 2 — Push & receive (outline; detailed plan written after PR 1 merges)

After PR 1 lands, write `2026-05-25-observability-cluster-topology-pr2-impl.md` containing bite-sized tasks for:

1. `LOGS_DIR/cluster_push_cursor/<destination_name>.json` cursor module (read, advance, atomic write-then-rename).
2. `push_logs_to_hub` management command: iterate active destinations, read local logs since cursor, batch up to `max_batch_bytes`, POST to hub, on 2xx advance cursor + update `last_push_at`/`last_push_status`.
3. Per-destination heartbeat registration: when a destination row is created (signal handler), register `cluster_push.<name>` with appropriate `max_age`.
4. `POST /cluster/logs/<stream>/` view: APIKey auth (existing middleware), JSONL parse + per-record validation, LRU dedup on `record_id`, cycle-back drop on `path` containing self, write to `LOGS_DIR/cluster/<source_instance_id>/<stream>.jsonl`.
5. LRU dedup cache: in-process; configurable via `OBSERVABILITY_DEDUP_CACHE_SIZE` / `_TTL`.
6. `cluster_status` command + admin link.
7. `O004` system check: walk active destinations, warn if `last_push_at` older than `OBSERVABILITY_CLUSTER_MAX_AGE`.
8. Unstub `bin/cli/cluster.sh` items 8 + 9.
9. Wire `cluster_push.<name>` heartbeats into `apps/observability/AGENTS.md` docs.
10. Verify + push + open PR 2.

Estimated 10 tasks, ~700 LOC.

---

## PR 3 — Forwarding (outline; detailed plan written after PR 2 merges)

After PR 2 lands, write `2026-05-25-observability-cluster-topology-pr3-impl.md` containing bite-sized tasks for:

1. Extend `push_logs_to_hub` source set: when `forward_received=True`, also read `LOGS_DIR/cluster/<source>/<stream>.jsonl` files (one cursor per source per destination).
2. Append local `instance_id` to `path[]` before push.
3. Loop break: skip records whose `path` contains `destination.api_key.owner_instance_id`.
4. `O005` system check: walk `LOGS_DIR/cluster/<instance>/heartbeats.jsonl` mtimes; warn on stale instances.
5. Tests for the four canonical loop scenarios:
   - Direct cycle: A → B → A
   - Three-node cycle: A → B → C → A
   - Legitimate diamond: X → A → C; X → B → C (must not double-store)
   - Forwarded-to-self: record arriving at receiver with receiver's own `instance_id` already in `path`
6. End-to-end integration test: 3 hosts, one regional aggregator, verify a record originated on `agent-eu-1` lands once at `central` despite traversing two hops.
7. Update `apps/observability/AGENTS.md` with the forwarding + loop-prevention contract documented in plain prose.
8. Verify + push + open PR 3.

Estimated 8 tasks, ~500 LOC.

---

## Skills referenced

- @superpowers:test-driven-development — every task in PR 1 is a TDD cycle (failing test → minimal impl → passing test → commit).
- @superpowers:verification-before-completion — final task per PR runs the full verification matrix before pushing.
- @superpowers:executing-plans — execution harness for this plan.
- @superpowers:subagent-driven-development — if executing PR 1 with subagent-per-task isolation.