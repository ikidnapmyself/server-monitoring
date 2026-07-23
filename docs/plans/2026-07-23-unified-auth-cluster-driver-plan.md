---
title: "Unified Auth + Cluster-as-Driver — Implementation Plan (Slice A)"
parent: Plans
---

# Unified Auth + Cluster-as-Driver Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collapse authentication onto a single Bearer API-key credential, make the cluster driver an ordinary always-registered driver, and stop a sibling push from making the hub check itself — without building the source router (deferred to Slice B).

**Architecture:** The API-key middleware becomes the one auth gate; `push_to_hub` sends a Bearer key instead of HMAC. `ClusterDriver` is registered unconditionally and declares `skip_checkers = True`, which the webhook path forwards into the pipeline run so the orchestrator drops the CHECK stage for cluster payloads. Dead `CLUSTER_ROLE` and `WEBHOOK_SECRET_CLUSTER` are retired in favour of `HUB_API_KEY`. The per-driver HMAC scaffold stays in the tree, dormant.

**Tech Stack:** Django 5.2, pytest + pytest-django, bats (shell tests), uv.

**Design doc:** `docs/plans/2026-07-23-unified-auth-cluster-driver-design.md`

**Conventions (from AGENTS.md):** absolute imports; 100% branch coverage on changed code; `uv run pytest`, `uv run black .`, `uv run ruff check .`; bats via `./bin/tests/test_helper/bats-core/bin/bats <file>`. Commit frequently. TDD throughout.

---

## Task 1: `create_api_key` management command

**Files:**
- Create: `config/management/__init__.py` (empty)
- Create: `config/management/commands/__init__.py` (empty)
- Create: `config/management/commands/create_api_key.py`
- Test: `config/_tests/commands/__init__.py` (empty, if missing) and `config/_tests/commands/test_create_api_key.py`

**Step 1: Write the failing test**

```python
# config/_tests/commands/test_create_api_key.py
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from config.models import APIKey


class CreateApiKeyCommandTests(TestCase):
    def test_mints_key_and_prints_raw_token_once(self):
        out = StringIO()
        call_command("create_api_key", "--name", "agent web-03", stdout=out)
        output = out.getvalue()

        # Exactly one key persisted, hash only (40 hex bytes = token_hex(20)).
        key = APIKey.objects.get(name="agent web-03")
        self.assertEqual(len(key.key), 64)  # sha256 hex digest

        # The raw 40-char token is printed once and is NOT the stored hash.
        self.assertIn(key.prefix, output)
        self.assertNotIn(key.key, output)  # never print the digest

    def test_requires_name(self):
        with self.assertRaises(Exception):
            call_command("create_api_key")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/commands/test_create_api_key.py -v`
Expected: FAIL — `Unknown command: 'create_api_key'`.

**Step 3: Write minimal implementation**

```python
# config/management/commands/create_api_key.py
"""Mint an API key and print the raw token once.

Usage:
    python manage.py create_api_key --name "agent web-03"
"""

from django.core.management.base import BaseCommand, CommandError

from config.models import APIKey


class Command(BaseCommand):
    help = "Create an API key and print its raw token (shown once, never stored)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Human-readable label for the key.")

    def handle(self, *args, **options):
        name = options["name"].strip()
        if not name:
            raise CommandError("--name must not be empty.")

        api_key = APIKey.objects.create(name=name)
        raw = getattr(api_key, "_raw_key", "")
        if not raw:  # pragma: no cover - defensive; save() always sets it on create
            raise CommandError("Key generation failed.")

        self.stdout.write(self.style.SUCCESS(f"API key '{name}' created."))
        self.stdout.write("")
        self.stdout.write("Raw token (shown once — store it now):")
        self.stdout.write(f"    {raw}")
        self.stdout.write("")
        self.stdout.write(f"Prefix (safe to reference): {api_key.prefix}")
```

Also create the empty `__init__.py` files listed above.

**Step 4: Run test to verify it passes**

Run: `uv run pytest config/_tests/commands/test_create_api_key.py -v`
Expected: PASS (both tests).

**Step 5: Commit**

```bash
git add config/management config/_tests/commands
git commit -m "feat(config): add create_api_key management command"
```

---

## Task 2: `skip_checkers` on drivers + orchestrator honours it

**Files:**
- Modify: `apps/alerts/drivers/base.py:66-71` (add class attribute)
- Modify: `apps/alerts/drivers/cluster.py` (set attribute on `ClusterDriver`)
- Modify: `apps/orchestration/orchestrator.py:262-265` (stage selection)
- Test: `apps/orchestration/_tests/` (mirror; use the existing orchestrator test module) and `apps/alerts/_tests/drivers/test_cluster.py`

**Step 1: Write the failing tests**

Driver attribute (add to the cluster driver test module):

```python
# apps/alerts/_tests/drivers/test_cluster.py  (add to existing file)
from apps.alerts.drivers.cluster import ClusterDriver
from apps.alerts.drivers.base import BaseAlertDriver


def test_cluster_declares_skip_checkers():
    assert ClusterDriver().skip_checkers is True


def test_base_driver_defaults_skip_checkers_false():
    assert BaseAlertDriver.skip_checkers is False
```

Orchestrator honours `skip_checkers` in the payload — add a unit test asserting the
stage list. Locate the existing orchestrator test module (search `run_pipeline`),
and add:

```python
def test_skip_checkers_payload_omits_check_stage(self):
    from apps.orchestration.orchestrator import PipelineOrchestrator, STAGE_ORDER
    from apps.orchestration.models import PipelineStage

    orch = PipelineOrchestrator()
    result = orch.run_pipeline(
        payload={"skip_checkers": True, "alerts": []},
        source="cluster",
    )
    run = result_to_run(result)  # or fetch PipelineRun by result.run_id
    stages = [e.stage for e in run.stage_executions.all()]
    assert PipelineStage.CHECK not in stages
    assert PipelineStage.NOTIFY in stages
```

> Note: match the assertion style already used in the orchestrator tests (they may
> assert on `result.stages_completed`). Prefer asserting `PipelineStage.CHECK not in
> result.stages_completed` if that attribute exists — read the existing test first.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/alerts/_tests/drivers/test_cluster.py apps/orchestration/_tests -k "skip_checkers" -v`
Expected: FAIL — `AttributeError: skip_checkers` and CHECK still present.

**Step 3: Write minimal implementation**

`apps/alerts/drivers/base.py` — add under the existing class attributes:

```python
class BaseAlertDriver(ABC):
    """Abstract base class for alert source drivers."""

    name: str = "base"
    signature_header: str | None = None
    signature_algorithm: str = "sha256"
    # Slice A interim rule (Slice B's router will generalise this): payloads from
    # this source already carry their own diagnostics, so the receiving instance
    # must not re-run local checkers against itself.
    skip_checkers: bool = False
```

`apps/alerts/drivers/cluster.py` — on `ClusterDriver`, next to `signature_header`:

```python
    name = "cluster"
    signature_header = "X-Cluster-Signature"
    skip_checkers = True
```

`apps/orchestration/orchestrator.py` — replace the stage-selection block:

```python
        # Determine which stages to run.
        checks_only = payload.get("checks_only", False)
        skip_checkers = payload.get("skip_checkers", False)
        if checks_only:
            active_stages = [PipelineStage.CHECK]
            final_status = PipelineStatus.CHECKED
        elif skip_checkers:
            active_stages = [s for s in STAGE_ORDER if s != PipelineStage.CHECK]
            final_status = PipelineStatus.NOTIFIED
        else:
            active_stages = STAGE_ORDER
            final_status = PipelineStatus.NOTIFIED
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/alerts/_tests/drivers/test_cluster.py apps/orchestration/_tests -k "skip_checkers" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/drivers/base.py apps/alerts/drivers/cluster.py apps/orchestration/orchestrator.py apps/alerts/_tests apps/orchestration/_tests
git commit -m "feat(pipeline): drivers can declare skip_checkers; orchestrator honours it"
```

---

## Task 3: Register `ClusterDriver` unconditionally

**Files:**
- Modify: `apps/alerts/drivers/__init__.py:44-55` (remove the `CLUSTER_ENABLED` gate)
- Test: `apps/alerts/_tests/drivers/test_registry.py` (create or extend)

**Step 1: Write the failing test**

```python
# apps/alerts/_tests/drivers/test_registry.py
from apps.alerts.drivers import DRIVER_REGISTRY
from apps.alerts.drivers.cluster import ClusterDriver


def test_cluster_is_always_registered():
    assert DRIVER_REGISTRY.get("cluster") is ClusterDriver
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/drivers/test_registry.py -v`
Expected: FAIL if the test environment has `CLUSTER_ENABLED` unset (cluster absent).

**Step 3: Write minimal implementation**

In `apps/alerts/drivers/__init__.py`, add `"cluster": ClusterDriver` directly to
`DRIVER_REGISTRY` and delete `_register_cluster_driver()` and its call:

```python
DRIVER_REGISTRY: dict[str, type[BaseAlertDriver]] = {
    "alertmanager": AlertManagerDriver,
    "grafana": GrafanaDriver,
    "pagerduty": PagerDutyDriver,
    "datadog": DatadogDriver,
    "newrelic": NewRelicDriver,
    "opsgenie": OpsGenieDriver,
    "zabbix": ZabbixDriver,
    "cluster": ClusterDriver,
    "generic": GenericWebhookDriver,
}
```

(Keep `generic` last — `detect_driver` still skips it until the end.)

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/drivers/ -v`
Expected: PASS. Also check no test relied on cluster being absent when
`CLUSTER_ENABLED=0` — search `_register_cluster_driver` and `CLUSTER_ENABLED` in
`apps/alerts/_tests` and update/remove stale cases.

**Step 5: Commit**

```bash
git add apps/alerts/drivers/__init__.py apps/alerts/_tests/drivers
git commit -m "refactor(alerts): register ClusterDriver unconditionally"
```

---

## Task 4: Webhook path forwards `skip_checkers`

**Files:**
- Modify: `apps/alerts/views.py` (after the driver is resolved, before enqueue ~line 58-90)
- Test: `apps/alerts/_tests/views/test_webhook.py` (extend)

**Step 1: Write the failing test**

Assert that a cluster payload enqueues a run whose payload carries
`skip_checkers=True`. Mock `run_pipeline_task.delay` and inspect kwargs. Read the
existing webhook test for the established mocking pattern first.

```python
def test_cluster_webhook_sets_skip_checkers(self):
    from unittest.mock import patch
    payload = {"source": "cluster", "instance_id": "web-03", "alerts": []}
    with patch("apps.orchestration.tasks.run_pipeline_task.delay") as delay:
        delay.return_value.id = "abc"
        self.client.post(
            "/alerts/webhook/cluster/",
            data=payload, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",  # from a created APIKey
        )
    sent_payload = delay.call_args.kwargs["payload"]
    assert sent_payload.get("skip_checkers") is True
```

> If `API_KEY_AUTH_ENABLED` defaults to on in tests, create an `APIKey` in `setUp`
> and pass its raw token, or `override_settings(API_KEY_AUTH_ENABLED=False)` for this
> unit — match what the existing webhook tests already do.

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/views/test_webhook.py -k skip_checkers -v`
Expected: FAIL — `skip_checkers` absent from the enqueued payload.

**Step 3: Write minimal implementation**

In `apps/alerts/views.py`, after `sig_driver` is resolved (the block that sets
`sig_driver` from `get_driver`/`detect_driver`), add:

```python
            # Interim (Slice A): a driver that already carries diagnostics tells the
            # pipeline to skip local checkers. Slice B replaces this with routing.
            if sig_driver and getattr(sig_driver, "skip_checkers", False):
                payload["skip_checkers"] = True
```

Place it before both the Celery-enqueue branch and the synchronous fallback so both
see the flag.

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/views/test_webhook.py -v`
Expected: PASS.

**Step 5: Verify the synchronous fallback**

Read `apps/alerts/services.py` `AlertOrchestrator.process_webhook`. If it runs
checkers directly, ensure it also respects `payload["skip_checkers"]`; if it does not
run checkers at all, note that in the commit message and move on (no change needed).

**Step 6: Commit**

```bash
git add apps/alerts/views.py apps/alerts/_tests/views/test_webhook.py
git commit -m "feat(alerts): webhook forwards driver skip_checkers into the run"
```

---

## Task 5: `push_to_hub` authenticates with a Bearer API key

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py:55` and the header block (~104-108)
- Test: `apps/alerts/_tests/commands/test_push_to_hub.py` (extend)

**Step 1: Write the failing test**

```python
def test_push_sends_bearer_key_and_no_signature(self):
    from unittest.mock import patch, MagicMock
    from django.test import override_settings

    with override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123"):
        with patch("apps.alerts.management.commands.push_to_hub.safe_urlopen") as urlopen:
            resp = MagicMock(); resp.status = 202; resp.read.return_value = b"{}"
            urlopen.return_value.__enter__.return_value = resp
            call_command("push_to_hub")
            request = urlopen.call_args.args[0]
            assert request.headers["Authorization"] == "Bearer tok123"
            assert "X-cluster-signature" not in {k.lower(): v for k, v in request.headers.items()}

def test_push_errors_without_api_key(self):
    from django.test import override_settings
    from django.core.management.base import CommandError
    with override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY=""):
        with self.assertRaises(CommandError):
            call_command("push_to_hub")
```

> `urllib.request.Request` capitalises header keys (`Authorization`). Assert with the
> exact casing `Request` stores, or normalise as above. Read the existing push_to_hub
> test to reuse its `safe_urlopen` mock.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -k "bearer or without_api_key" -v`
Expected: FAIL — still reads `WEBHOOK_SECRET_CLUSTER` / sends HMAC.

**Step 3: Write minimal implementation**

Replace the secret line (`push_to_hub.py:55`):

```python
        api_key = getattr(settings, "HUB_API_KEY", "")
        if not api_key:
            raise CommandError(
                "HUB_API_KEY is not configured. Set it in .env to enable agent mode."
            )
```

Replace the header-building block (drop HMAC):

```python
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
```

Remove now-unused imports (`hmac`, `hashlib`) if nothing else uses them — run ruff to confirm.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "feat(cluster): push_to_hub authenticates with Bearer HUB_API_KEY, drops HMAC"
```

---

## Task 6: Settings — add `HUB_API_KEY`, retire `WEBHOOK_SECRET_CLUSTER` & `CLUSTER_ROLE`

**Files:**
- Modify: `config/settings.py:211-214`
- Test: covered indirectly; add a settings smoke assertion in `config/_tests/` if a settings test module exists.

**Step 1: Implementation**

In `config/settings.py`, replace the cluster block:

```python
CLUSTER_ENABLED = os.environ.get("CLUSTER_ENABLED", "0") == "1"
HUB_URL = os.environ.get("HUB_URL", "")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
HUB_API_KEY = os.environ.get("HUB_API_KEY", "")
```

Delete the `WEBHOOK_SECRET_CLUSTER` line. Grep the tree for any remaining
`WEBHOOK_SECRET_CLUSTER` or `CLUSTER_ROLE` reader and confirm only intended edits remain:

Run: `git grep -n "WEBHOOK_SECRET_CLUSTER\|CLUSTER_ROLE" -- ':!docs/plans/*'`
Expected after all tasks: no non-test, non-historical hits.

**Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (fix any test that referenced `WEBHOOK_SECRET_CLUSTER`).

**Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat(config): add HUB_API_KEY setting, retire WEBHOOK_SECRET_CLUSTER"
```

---

## Task 7: Preflight cluster checks

**Files:**
- Modify: `apps/checkers/preflight/checks.py:383-430` (`check_cluster_coherence`)
- Test: `apps/checkers/_tests/preflight/test_checks.py` (update cluster cases)

**Step 1: Update the failing tests**

Change the existing cluster-coherence tests to expect `HUB_API_KEY` instead of
`WEBHOOK_SECRET_CLUSTER`: agent mode warns when `HUB_API_KEY` is empty; hub mode no
longer errors on a missing secret (auth is now the API-key middleware + a created
`APIKey`, which preflight cannot see, so downgrade the hub check to informational).

**Step 2: Run to verify they fail**

Run: `uv run pytest apps/checkers/_tests/preflight/test_checks.py -k cluster -v`
Expected: FAIL.

**Step 3: Implementation**

Rewrite `check_cluster_coherence` to read `HUB_API_KEY` for agent mode and drop the
`WEBHOOK_SECRET_CLUSTER` reads:

```python
def check_cluster_coherence() -> list[CheckResult]:
    results: list[CheckResult] = []
    hub_url = getattr(settings, "HUB_URL", "")
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)
    api_key = getattr(settings, "HUB_API_KEY", "")
    instance_id = getattr(settings, "INSTANCE_ID", "")

    if hub_url and cluster_enabled:
        return [CheckResult(level="error",
                            message="Cluster conflict: both HUB_URL and CLUSTER_ENABLED=1",
                            hint="An instance cannot be both agent and hub.")]

    if hub_url:  # agent
        if not api_key:
            results.append(CheckResult(level="warn",
                message="Agent mode: HUB_API_KEY is empty",
                hint="Set it to the token created on the hub via create_api_key."))
        if not instance_id:
            results.append(CheckResult(level="warn",
                message="Agent mode: INSTANCE_ID is empty",
                hint="Set it to identify this agent."))

    if cluster_enabled and not hub_url:  # hub
        results.append(CheckResult(level="ok",
            message="Hub mode: ensure API_KEY_AUTH_ENABLED=1 and an APIKey exists for agents"))

    if not results:
        role = "agent" if hub_url else ("hub" if cluster_enabled else "standalone")
        results.append(CheckResult(level="ok", message=f"Cluster: {role}"))
    return results
```

**Step 4: Run to verify they pass**

Run: `uv run pytest apps/checkers/_tests/preflight/test_checks.py -k cluster -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/preflight/checks.py apps/checkers/_tests/preflight/test_checks.py
git commit -m "feat(preflight): cluster checks use HUB_API_KEY, not WEBHOOK_SECRET_CLUSTER"
```

---

## Task 8: Installer cluster step

**Files:**
- Modify: `bin/install/cluster.sh`
- Test: `bin/tests/` (add `bin/tests/test_cluster.bats` if none, or extend an existing one)

**Step 1: Write the failing bats test**

Assert the script no longer writes `CLUSTER_ROLE` or `WEBHOOK_SECRET_CLUSTER`, and
that hub role mentions `create_api_key`. Since the script is interactive, test the
non-interactive assertions you can (e.g. `--help`/syntax, and grep the script for the
removed tokens). Minimum viable regression:

```bash
@test "cluster.sh no longer references CLUSTER_ROLE or WEBHOOK_SECRET_CLUSTER" {
    run grep -E "CLUSTER_ROLE|WEBHOOK_SECRET_CLUSTER" "$BIN_DIR/install/cluster.sh"
    assert_failure   # grep finds nothing → exit 1
}

@test "cluster.sh references HUB_API_KEY and create_api_key" {
    run grep -q "HUB_API_KEY" "$BIN_DIR/install/cluster.sh"; assert_success
    run grep -q "create_api_key" "$BIN_DIR/install/cluster.sh"; assert_success
}
```

**Step 2: Run to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_cluster.bats`
Expected: FAIL.

**Step 3: Implementation**

Rewrite `bin/install/cluster.sh` role handling:
- Drop the `CLUSTER_ROLE` prompt and `dotenv_set ... CLUSTER_ROLE`.
- Keep role selection locally (agent/hub/both) only to branch prompts.
- **Agent/both:** prompt `HUB_URL`, `INSTANCE_ID`, and `HUB_API_KEY` (masked) instead of `WEBHOOK_SECRET_CLUSTER`.
- **Hub/both:** set `CLUSTER_ENABLED=1`; print guidance to mint a key:
  `uv run python manage.py create_api_key --name "<agent-name>"` and paste the token into each agent's `HUB_API_KEY`.
- Keep the agent `push_to_hub --dry-run` verification.

**Step 4: Run to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_cluster.bats`
Expected: PASS. Also `bash -n bin/install/cluster.sh`.

**Step 5: Commit**

```bash
git add bin/install/cluster.sh bin/tests/test_cluster.bats
git commit -m "feat(installer): cluster step uses HUB_API_KEY + create_api_key, drops dead knobs"
```

---

## Task 9: `.env.sample`

**Files:**
- Modify: `.env.sample:18,38-43`

**Step 1: Implementation**

- Line 18: `API_KEY_AUTH_ENABLED=1`.
- Replace the cluster block:

```dotenv
# Cluster (multi-instance) — see docs. An agent pushes to a hub; a hub accepts pushes.
# Agent: set HUB_URL + INSTANCE_ID + HUB_API_KEY (token created on the hub).
# Hub:   set CLUSTER_ENABLED=1 and create an APIKey via `manage.py create_api_key`.
# HUB_URL=https://monitoring-hub.example.com
# CLUSTER_ENABLED=0
# INSTANCE_ID=
# HUB_API_KEY=
```

**Step 2: Verify**

Run: `git grep -n "WEBHOOK_SECRET_CLUSTER\|CLUSTER_ROLE" .env.sample`
Expected: no hits.

**Step 3: Commit**

```bash
git add .env.sample
git commit -m "docs(env): default API_KEY_AUTH_ENABLED=1; cluster uses HUB_API_KEY"
```

---

## Task 10: `set_production.sh` ensures auth on

**Files:**
- Modify: `bin/set_production.sh` (add step after DEBUG=0)
- Test: `bin/tests/test_set_production.bats` (extend)

**Step 1: Write the failing test**

```bash
@test "set_production.sh sets API_KEY_AUTH_ENABLED=1" {
    local tmp; tmp="$(mktemp -d)"
    printf 'DJANGO_ENV=dev\nDJANGO_SECRET_KEY=x\nDJANGO_ALLOWED_HOSTS=e.com\n' > "$tmp/.env"
    stub="$(mktemp -d)"; printf '#!/usr/bin/env bash\necho uv "$*"\n' > "$stub/uv"; chmod +x "$stub/uv"
    export PROJECT_DIR="$tmp"
    PATH="$stub:$PATH" run "$BIN_DIR/set_production.sh"
    assert_success
    run grep -q "API_KEY_AUTH_ENABLED=1" "$tmp/.env"; assert_success
}
```

**Step 2: Run to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_set_production.bats`
Expected: FAIL.

**Step 3: Implementation**

After the `DJANGO_DEBUG=0` block in `bin/set_production.sh`:

```bash
# API key auth on in production (config.W002)
dotenv_set "$ENV_FILE" "API_KEY_AUTH_ENABLED" "1"
CHANGES+=("API_KEY_AUTH_ENABLED=1")
```

Update the `--help` text list accordingly.

**Step 4: Run to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_set_production.bats`
Expected: PASS.

**Step 5: Commit**

```bash
git add bin/set_production.sh bin/tests/test_set_production.bats
git commit -m "feat(set-production): enable API_KEY_AUTH_ENABLED so W002 clears"
```

---

## Task 11: Docs — cluster migration note

**Files:**
- Modify: `docs/Deployment.md` (cluster section, if present) or `apps/alerts/AGENTS.md`
- Modify: `AGENTS.md` cluster references if any mention `WEBHOOK_SECRET_CLUSTER`

**Step 1: Implementation**

Add a short "Cluster auth (migration)" note: agents authenticate with `HUB_API_KEY`
(a Bearer token created on the hub via `manage.py create_api_key`); `WEBHOOK_SECRET_CLUSTER`
and `CLUSTER_ROLE` are removed. Cutover: create key → set `HUB_API_KEY` on each agent →
ensure `API_KEY_AUTH_ENABLED=1` on the hub → verify with `push_to_hub --dry-run`.

Grep docs for stale references:

Run: `git grep -n "WEBHOOK_SECRET_CLUSTER\|CLUSTER_ROLE" -- docs ':!docs/plans/*'`
Update non-historical hits. (Leave `docs/plans/*` untouched — historical records.)

**Step 2: Commit**

```bash
git add docs AGENTS.md apps/alerts/AGENTS.md
git commit -m "docs: cluster auth migration to HUB_API_KEY"
```

---

## Task 12: Full verification

**Step 1: Run everything**

```bash
uv run black . --check
uv run ruff check .
uv run pytest -q
uv run coverage run -m pytest && uv run coverage report   # 100% on changed lines
./bin/tests/test_helper/bats-core/bin/bats bin/tests/*.bats
uv run pip-audit --strict --desc
uv run python manage.py check           # config.W002 gone when API_KEY_AUTH_ENABLED=1 + prod
```

**Step 2: Manual smoke (optional, local)**

```bash
uv run python manage.py create_api_key --name "smoke"
# copy token; with API_KEY_AUTH_ENABLED=1:
#   POST /alerts/webhook/cluster/ without Bearer -> 401
#   POST with Bearer <token> and a cluster payload -> 202/200, no CHECK stage in the run
```

**Step 3: Acceptance-criteria checklist** (from the design doc §Acceptance criteria) — confirm all six.

**Step 4: Final commit / open PR**

```bash
git push -u origin design/unified-auth-cluster-driver
gh pr create --base main --title "feat: unified API-key auth + cluster-as-driver (slice A)" --body "<summary + link to design doc>"
```

---

## Notes for the executor

- **Read the existing test module before writing each test** — reuse the established
  fixtures (APIKey creation, `safe_urlopen` mock, orchestrator result helpers). The
  snippets above are illustrative; match local style and assertion helpers.
- **`WEBHOOK_SECRET_CLUSTER` / `CLUSTER_ROLE` must end with zero non-historical hits** —
  the Task 6 and Task 11 greps are the gate.
- **Do not touch `docs/plans/*`** except to add this plan — they are immutable history.
- **Coverage:** every changed branch needs a test (100% on changed lines). The
  `skip_checkers` elif and the `HUB_API_KEY`-missing error are easy misses.
