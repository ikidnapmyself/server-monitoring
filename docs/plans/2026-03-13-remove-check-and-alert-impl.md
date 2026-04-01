---
title: "2026-03-13 Remove check_and_alert Implementation Plan"
parent: Plans
---

# Remove `check_and_alert` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the `check_and_alert` management command, absorb its unique CLI flags into `run_pipeline`, and update all references so all executions are tracked through orchestration.

**Architecture:** The `check_and_alert` command bypasses orchestration by calling `CheckAlertBridge` directly. We remove the command, add its flags (`--checkers`, `--hostname`, `--label`, `--no-incidents`, `--warning-threshold`, `--critical-threshold`) to `run_pipeline`, and wire them through the payload so `CheckExecutor` passes them to `CheckAlertBridge`. All doc/script references get updated.

**Tech Stack:** Django management commands, Python, shell scripts (bash)

---

### Task 1: Add checker flags to `run_pipeline` command

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`

**Step 1: Add new arguments to `add_arguments`**

In `run_pipeline.py`, after the `--json` argument (around line 103), add:

```python
        parser.add_argument(
            "--checkers",
            nargs="+",
            help="Specific checkers to run (e.g., cpu memory disk). Only used with --checks-only.",
        )
        parser.add_argument(
            "--hostname",
            type=str,
            help="Override hostname in alert labels.",
        )
        parser.add_argument(
            "--label",
            action="append",
            dest="labels",
            metavar="KEY=VALUE",
            help="Additional label for alerts (can be repeated).",
        )
        parser.add_argument(
            "--no-incidents",
            action="store_true",
            help="Skip automatic incident creation.",
        )
        parser.add_argument(
            "--warning-threshold",
            type=float,
            help="Override warning threshold for all checkers.",
        )
        parser.add_argument(
            "--critical-threshold",
            type=float,
            help="Override critical threshold for all checkers.",
        )
```

**Step 2: Wire flags into payload in `_get_payload`**

In the `_get_payload` method, update the return dict (around line 210) to include the new flags:

```python
        # Parse labels
        labels = {}
        if options.get("labels"):
            for label in options["labels"]:
                if "=" not in label:
                    raise CommandError(f"Invalid label format: {label}. Use KEY=VALUE.")
                key, value = label.split("=", 1)
                labels[key] = value

        # Build checker configs from threshold overrides
        checker_names = options.get("checkers")
        checker_configs = {}
        if checker_names:
            for name in checker_names:
                config = {}
                if options.get("warning_threshold") is not None:
                    config["warning_threshold"] = options["warning_threshold"]
                if options.get("critical_threshold") is not None:
                    config["critical_threshold"] = options["critical_threshold"]
                if config:
                    checker_configs[name] = config
        else:
            # Apply thresholds to all checkers if no specific checkers selected
            if options.get("warning_threshold") is not None or options.get("critical_threshold") is not None:
                checker_configs["__all__"] = {}
                if options.get("warning_threshold") is not None:
                    checker_configs["__all__"]["warning_threshold"] = options["warning_threshold"]
                if options.get("critical_threshold") is not None:
                    checker_configs["__all__"]["critical_threshold"] = options["critical_threshold"]

        return {
            "payload": inner_payload,
            "driver": options["source"] if options["source"] != "cli" else None,
            "checker_names": checker_names,
            "checker_configs": checker_configs if checker_configs else None,
            "labels": labels if labels else None,
            "hostname": options.get("hostname"),
            "no_incidents": options.get("no_incidents", False),
        }
```

**Step 3: Run existing tests to confirm nothing breaks**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py -v`
Expected: All existing tests PASS

**Step 4: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: add checker flags to run_pipeline command"
```

---

### Task 2: Wire `hostname` and `no_incidents` through `CheckExecutor`

**Files:**
- Modify: `apps/orchestration/executors.py`

**Step 1: Write failing test**

Add to `apps/orchestration/_tests/test_executors.py`:

```python
def test_check_executor_passes_hostname_and_no_incidents(self):
    """CheckExecutor passes hostname and no_incidents to CheckAlertBridge."""
    mock_bridge = mock.Mock()
    mock_bridge.run_checks_and_alert.return_value = mock.Mock(
        checks_run=1,
        errors=[],
        check_results=[],
    )

    ctx = StageContext(
        trace_id="t",
        run_id="r",
        payload={
            "hostname": "web-01",
            "no_incidents": True,
            "checker_names": ["cpu"],
        },
    )

    with patch(
        "apps.alerts.check_integration.CheckAlertBridge",
        return_value=mock_bridge,
    ) as mock_cls:
        executor = CheckExecutor()
        executor.execute(ctx)

    mock_cls.assert_called_once_with(
        hostname="web-01",
        auto_create_incidents=False,
    )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_executors.py::CheckExecutorTest::test_check_executor_passes_hostname_and_no_incidents -v`
Expected: FAIL — `CheckAlertBridge()` is called with no args

**Step 3: Modify `CheckExecutor.execute` in `executors.py`**

Replace lines 110-113 (the `CheckAlertBridge` instantiation block):

```python
            from apps.alerts.check_integration import CheckAlertBridge

            hostname = payload.get("hostname")
            no_incidents = payload.get("no_incidents", False)

            bridge_kwargs = {}
            if hostname:
                bridge_kwargs["hostname"] = hostname
            bridge_kwargs["auto_create_incidents"] = not no_incidents

            bridge = CheckAlertBridge(**bridge_kwargs)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_executors.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/orchestration/executors.py apps/orchestration/_tests/test_executors.py
git commit -m "feat: wire hostname and no_incidents through CheckExecutor"
```

---

### Task 3: Write tests for new `run_pipeline` flags

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write tests for the new flags**

Add these tests to `RunPipelineCommandTest`:

```python
@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_checks_only_with_checkers_flag(self, mock_orchestrator):
    """--checks-only with --checkers passes checker_names in payload."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 1.0
    mock_result.ingest = None
    mock_result.check = {"checks_run": 1, "checks_passed": 1, "checks_failed": 0, "duration_ms": 1}
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = []
    mock_result.to_dict.return_value = {"status": "COMPLETED"}
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command("run_pipeline", "--checks-only", "--checkers", "cpu", "memory", stdout=out)

    call_args = mock_orchestrator.return_value.run_pipeline.call_args
    payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
    self.assertEqual(payload["checker_names"], ["cpu", "memory"])

@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_checks_only_with_hostname_flag(self, mock_orchestrator):
    """--hostname is passed through the payload."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 1.0
    mock_result.ingest = None
    mock_result.check = {"checks_run": 1, "checks_passed": 1, "checks_failed": 0, "duration_ms": 1}
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = []
    mock_result.to_dict.return_value = {"status": "COMPLETED"}
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command("run_pipeline", "--checks-only", "--hostname", "web-01", stdout=out)

    call_args = mock_orchestrator.return_value.run_pipeline.call_args
    payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
    self.assertEqual(payload["hostname"], "web-01")

@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_checks_only_with_labels(self, mock_orchestrator):
    """--label KEY=VALUE flags are parsed and passed in payload."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 1.0
    mock_result.ingest = None
    mock_result.check = {"checks_run": 1, "checks_passed": 1, "checks_failed": 0, "duration_ms": 1}
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = []
    mock_result.to_dict.return_value = {"status": "COMPLETED"}
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command(
        "run_pipeline", "--checks-only",
        "--label", "env=production", "--label", "team=sre",
        stdout=out,
    )

    call_args = mock_orchestrator.return_value.run_pipeline.call_args
    payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
    self.assertEqual(payload["labels"], {"env": "production", "team": "sre"})

def test_invalid_label_format_raises_error(self):
    """--label without = raises CommandError."""
    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command("run_pipeline", "--checks-only", "--label", "badlabel", stdout=out)
    self.assertIn("KEY=VALUE", str(ctx.exception))

@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_checks_only_with_no_incidents(self, mock_orchestrator):
    """--no-incidents flag is passed in payload."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 1.0
    mock_result.ingest = None
    mock_result.check = {"checks_run": 1, "checks_passed": 1, "checks_failed": 0, "duration_ms": 1}
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = []
    mock_result.to_dict.return_value = {"status": "COMPLETED"}
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command("run_pipeline", "--checks-only", "--no-incidents", stdout=out)

    call_args = mock_orchestrator.return_value.run_pipeline.call_args
    payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
    self.assertTrue(payload["no_incidents"])

@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_checks_only_with_threshold_overrides(self, mock_orchestrator):
    """--warning-threshold and --critical-threshold are passed as checker_configs."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 1.0
    mock_result.ingest = None
    mock_result.check = {"checks_run": 1, "checks_passed": 1, "checks_failed": 0, "duration_ms": 1}
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = []
    mock_result.to_dict.return_value = {"status": "COMPLETED"}
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command(
        "run_pipeline", "--checks-only",
        "--warning-threshold", "60", "--critical-threshold", "80",
        stdout=out,
    )

    call_args = mock_orchestrator.return_value.run_pipeline.call_args
    payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
    self.assertIn("__all__", payload["checker_configs"])
    self.assertEqual(payload["checker_configs"]["__all__"]["warning_threshold"], 60.0)
    self.assertEqual(payload["checker_configs"]["__all__"]["critical_threshold"], 80.0)
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py -v -k "test_checks_only_with_checkers_flag or test_checks_only_with_hostname_flag or test_checks_only_with_labels or test_invalid_label_format or test_checks_only_with_no_incidents or test_checks_only_with_threshold"`
Expected: All PASS (since Task 1 already added the flags)

**Step 3: Commit**

```bash
git add apps/orchestration/_tests/test_run_pipeline_command.py
git commit -m "test: add tests for new run_pipeline checker flags"
```

---

### Task 4: Delete `check_and_alert` command

**Files:**
- Delete: `apps/alerts/management/commands/check_and_alert.py`

**Step 1: Delete the file**

```bash
rm apps/alerts/management/commands/check_and_alert.py
```

**Step 2: Run full test suite to verify nothing imports the deleted command**

Run: `uv run pytest -v`
Expected: All PASS (no test imports `check_and_alert` — tests only exist for `CheckAlertBridge`)

**Step 3: Commit**

```bash
git add -u apps/alerts/management/commands/check_and_alert.py
git commit -m "refactor: remove check_and_alert command (absorbed into run_pipeline)"
```

---

### Task 5: Update `bin/setup_cron.sh`

**Files:**
- Modify: `bin/setup_cron.sh`

**Step 1: Replace the cron command on line 96**

Change:
```bash
CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py check_and_alert --json >> $PROJECT_DIR/cron.log 2>&1"
```
To:
```bash
CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py run_pipeline --checks-only --json >> $PROJECT_DIR/cron.log 2>&1"
```

**Step 2: Commit**

```bash
git add bin/setup_cron.sh
git commit -m "chore: update cron to use run_pipeline --checks-only"
```

---

### Task 6: Update `bin/setup_aliases.sh`

**Files:**
- Modify: `bin/setup_aliases.sh`

**Step 1: Replace alias on line 99**

Change:
```bash
alias ${prefix}-check-and-alert='cd "${PROJECT_DIR}" && uv run python manage.py check_and_alert'
```
To:
```bash
alias ${prefix}-check-and-alert='cd "${PROJECT_DIR}" && uv run python manage.py run_pipeline --checks-only'
```

**Step 2: Commit**

```bash
git add bin/setup_aliases.sh
git commit -m "chore: update check-and-alert alias to use run_pipeline"
```

---

### Task 7: Update `bin/cli.sh`

**Files:**
- Modify: `bin/cli.sh`

**Step 1: Rewrite `check_and_alert_menu()` (lines 350-386)**

Replace the entire function with one that calls `run_pipeline --checks-only` with appropriate flags:

```bash
check_and_alert_menu() {
    show_banner
    echo -e "${BOLD}═══ Run Checks Pipeline ═══${NC}"
    echo ""
    echo "Run health checks through the orchestrated pipeline"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --checkers NAME...     Specific checkers to run"
    echo "  --hostname=HOST        Override hostname in labels"
    echo "  --no-incidents         Skip incident creation"
    echo ""

    local options=(
        "Run all checks"
        "Run specific checkers"
        "Run all checks (dry run)"
        "Run all checks (JSON output)"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only"
                ;;
            2)
                read -p "Enter checker names (space-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only --checkers $checker_names"
                else
                    echo -e "${RED}Checker names required${NC}"
                fi
                ;;
            3)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run"
                ;;
            4)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --json"
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Update the menu item text on line 284**

Change:
```bash
        "check_and_alert - Run checker and create alert"
```
To:
```bash
        "check_and_alert - Run checks pipeline"
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "chore: update cli.sh to use run_pipeline --checks-only"
```

---

### Task 8: Update `bin/README.md`

**Files:**
- Modify: `bin/README.md`

**Step 1: Update references**

On line 15, change:
```
| `sm-check-and-alert` | `check_and_alert` | alerts | Run checks and create alerts/incidents |
```
To:
```
| `sm-check-and-alert` | `run_pipeline --checks-only` | orchestration | Run checks through orchestrated pipeline |
```

On line 29, change:
```
- [`apps/alerts/README.md`](../apps/alerts/README.md) — `check_and_alert` (9 flags)
```
To:
```
- [`apps/orchestration/README.md`](../apps/orchestration/README.md) — `run_pipeline` (includes checker flags)
```

On line 130, change:
```
- Writes crontab entry for `check_and_alert --json`
```
To:
```
- Writes crontab entry for `run_pipeline --checks-only --json`
```

**Step 2: Commit**

```bash
git add bin/README.md
git commit -m "docs: update bin/README.md references from check_and_alert to run_pipeline"
```

---

### Task 9: Update `apps/alerts/README.md`

**Files:**
- Modify: `apps/alerts/README.md`

**Step 1: Replace the `check_and_alert` CLI reference section (lines 342-446)**

Replace the entire section with a redirect:

```markdown
### Checks via Pipeline

The health-check-to-alert workflow is now handled by the orchestrated pipeline. Use:

```bash
# Run all checks through the pipeline
uv run python manage.py run_pipeline --checks-only

# Run specific checkers
uv run python manage.py run_pipeline --checks-only --checkers cpu memory disk

# Dry run
uv run python manage.py run_pipeline --checks-only --dry-run --json

# Custom labels and hostname
uv run python manage.py run_pipeline --checks-only --label env=production --hostname web-01

# Skip incidents
uv run python manage.py run_pipeline --checks-only --no-incidents

# Threshold overrides
uv run python manage.py run_pipeline --checks-only --warning-threshold 60 --critical-threshold 80
```

See [`apps/orchestration/README.md`](../orchestration/README.md) for full `run_pipeline` documentation.
```

**Step 2: Update the cron examples section (lines 516-522)**

Replace:
```
*/5 * * * * cd /path/to/project && python manage.py check_and_alert --json >> /var/log/health-checks.log 2>&1
* * * * * cd /path/to/project && python manage.py check_and_alert --checkers cpu memory --json
```
With:
```
*/5 * * * * cd /path/to/project && uv run python manage.py run_pipeline --checks-only --json >> /var/log/health-checks.log 2>&1
* * * * * cd /path/to/project && uv run python manage.py run_pipeline --checks-only --checkers cpu memory --json
```

**Step 3: Commit**

```bash
git add apps/alerts/README.md
git commit -m "docs: update alerts README to reference run_pipeline --checks-only"
```

---

### Task 10: Update remaining docs

**Files:**
- Modify: `README.md`
- Modify: `docs/Architecture.md`
- Modify: `docs/Installation.md`
- Modify: `docs/Setup-Guide.md`
- Modify: `apps/checkers/README.md`

**Step 1: Update `README.md` line 66**

Change:
```bash
uv run python manage.py check_and_alert
```
To:
```bash
uv run python manage.py run_pipeline --checks-only
```

**Step 2: Update `docs/Architecture.md` line 79**

Change:
```
| `check_and_alert` | alerts | Run checks and create alerts from results. Flags: `--dry-run`, `--no-incidents`, `--checkers` |
```
To:
```
| `run_pipeline --checks-only` | orchestration | Run checks through pipeline. Additional flags: `--checkers`, `--no-incidents`, `--hostname`, `--label`, `--warning-threshold`, `--critical-threshold` |
```

**Step 3: Update `docs/Installation.md` lines 81 and 248**

Change both occurrences of:
```bash
uv run python manage.py check_and_alert --json
```
To:
```bash
uv run python manage.py run_pipeline --checks-only --json
```

**Step 4: Update `docs/Setup-Guide.md`**

Lines 69 and 255 — change:
```
2) Local crontab  (check_and_alert via cron)
```
To:
```
2) Local crontab  (run_pipeline --checks-only via cron)
```

Lines 223 — change:
```bash
uv run python manage.py check_and_alert --json
```
To:
```bash
uv run python manage.py run_pipeline --checks-only --json
```

**Step 5: Update `apps/checkers/README.md` line 65**

Change:
```
> **Note:** Check runs are audit records and cannot be added/edited manually. They are created automatically when checks are run via management commands or the CheckAlertBridge.
```
To:
```
> **Note:** Check runs are audit records and cannot be added/edited manually. They are created automatically when checks are run via management commands or the orchestration pipeline.
```

**Step 6: Commit**

```bash
git add README.md docs/Architecture.md docs/Installation.md docs/Setup-Guide.md apps/checkers/README.md
git commit -m "docs: update all references from check_and_alert to run_pipeline --checks-only"
```

---

### Task 11: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Replace the `check_and_alert` reference in Essential Commands**

The current line:
```bash
uv run python manage.py check_health --list
```

Confirm there's no `check_and_alert` reference in CLAUDE.md. If there is, replace with `run_pipeline --checks-only`.

**Step 2: Commit (if changes needed)**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md references"
```

---

### Task 12: Run full test suite and coverage check

**Step 1: Run full tests**

Run: `uv run pytest -v`
Expected: All PASS

**Step 2: Run coverage**

Run: `uv run coverage run -m pytest && uv run coverage report`
Expected: No regression — `check_and_alert.py` is gone so its uncovered lines disappear.

**Step 3: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: All PASS

---

### Task 13: Update `apps/alerts/tasks.py` docstring

**Files:**
- Modify: `apps/alerts/tasks.py`

**Step 1: Update the docstring on line 103**

Change:
```python
    In this starter implementation, we run all checks via CheckAlertBridge.
    This already has solid logic for executing checkers and creating alerts.
```
To:
```python
    Runs all checks via CheckAlertBridge, which handles executing checkers
    and creating alerts.
```

**Step 2: Commit**

```bash
git add apps/alerts/tasks.py
git commit -m "docs: simplify tasks.py docstring"
```