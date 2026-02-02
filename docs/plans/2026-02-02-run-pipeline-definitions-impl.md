# Run Pipeline Definitions CLI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend `run_pipeline.py` to support definition-based pipelines via `--definition` and `--config` flags.

**Architecture:** Add two new arguments to the existing command. Selection logic determines which orchestrator to use based on flags. Reuse existing payload handling; add definition-specific dry-run and result display methods.

**Tech Stack:** Django management commands, `DefinitionBasedOrchestrator`, `PipelineDefinition` model

---

## Task 1: Add Tests for Definition Flag Validation

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write failing tests for new argument validation**

Add to `test_run_pipeline_command.py`:

```python
def test_run_pipeline_definition_not_found(self):
    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command("run_pipeline", "--definition", "nonexistent", stdout=out)
    self.assertIn("Pipeline definition not found", str(ctx.exception))

def test_run_pipeline_config_file_not_found(self):
    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command("run_pipeline", "--config", "missing.json", stdout=out)
    self.assertIn("Config file not found", str(ctx.exception))

def test_run_pipeline_definition_and_config_mutually_exclusive(self):
    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command(
            "run_pipeline",
            "--definition", "test",
            "--config", "test.json",
            stdout=out
        )
    self.assertIn("Cannot specify both", str(ctx.exception))
```

**Step 2: Run tests to verify they fail**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py -v -k "definition or config"`
Expected: FAIL (unrecognized arguments)

**Step 3: Commit failing tests**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add failing tests for definition flag validation"
```

---

## Task 2: Add New Command Arguments

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py:34-87`

**Step 1: Add `--definition` and `--config` arguments**

In `add_arguments` method, add after `--json`:

```python
parser.add_argument(
    "--definition",
    type=str,
    help="Name of a PipelineDefinition to run (from database)",
)
parser.add_argument(
    "--config",
    type=str,
    help="Path to JSON file containing pipeline definition config",
)
```

**Step 2: Run tests to check arguments are recognized**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py -v -k "definition or config"`
Expected: FAIL (but now with different error - missing validation logic)

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: add --definition and --config arguments to run_pipeline"
```

---

## Task 3: Add Mutual Exclusivity Validation

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py:89-119`

**Step 1: Add validation in handle() method**

At the start of `handle()` method, before `payload = self._get_payload(options)`:

```python
# Validate mutually exclusive definition options
if options.get("definition") and options.get("config"):
    raise CommandError("Cannot specify both --definition and --config")
```

**Step 2: Run mutual exclusivity test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_definition_and_config_mutually_exclusive -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: add mutual exclusivity validation for definition flags"
```

---

## Task 4: Add Definition Loading Logic

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`

**Step 1: Add imports at top of file**

```python
from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition
```

**Step 2: Add `_get_definition()` method**

Add after `_get_sample_payload()` method:

```python
def _get_definition(self, options) -> tuple[PipelineDefinition | None, dict | None]:
    """
    Load pipeline definition from database or config file.

    Returns:
        Tuple of (PipelineDefinition or None, config_path or None)
    """
    if options.get("definition"):
        name = options["definition"]
        try:
            definition = PipelineDefinition.objects.get(name=name)
            return definition, None
        except PipelineDefinition.DoesNotExist:
            raise CommandError(f"Pipeline definition not found: {name}")

    if options.get("config"):
        config_path = options["config"]
        try:
            with open(config_path) as f:
                config = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"Config file not found: {config_path}")
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON in config file: {e}")

        # Create an unsaved PipelineDefinition for execution
        definition = PipelineDefinition(
            name=f"__adhoc__{config_path}",
            config=config,
        )
        return definition, config_path

    return None, None
```

**Step 3: Run definition loading tests**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py -v -k "definition_not_found or config_file_not_found"`
Expected: PASS

**Step 4: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: add definition loading from database and config file"
```

---

## Task 5: Add Tests for Definition Dry-Run

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write failing test for definition dry-run**

```python
def test_run_pipeline_definition_dry_run(self):
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="test-pipeline",
        config={
            "version": "1.0",
            "nodes": [
                {"id": "ctx", "type": "context", "config": {"include": ["cpu"]}, "next": "notify"},
                {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
            ]
        }
    )

    out = io.StringIO()
    call_command("run_pipeline", "--definition", "test-pipeline", "--dry-run", stdout=out)
    output = out.getvalue()
    self.assertIn("=== DRY RUN ===", output)
    self.assertIn("Pipeline Definition: test-pipeline", output)
    self.assertIn("[context] ctx", output)
    self.assertIn("[notify] notify", output)
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_definition_dry_run -v`
Expected: FAIL

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add failing test for definition dry-run output"
```

---

## Task 6: Implement Definition Dry-Run Display

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`

**Step 1: Add `_show_definition_dry_run()` method**

Add after `_show_dry_run()` method:

```python
def _show_definition_dry_run(
    self,
    definition: "PipelineDefinition",
    payload: dict,
    options: dict,
    config_path: str | None = None,
):
    """Display what would happen in a definition-based dry run."""
    self.stdout.write(self.style.WARNING("=== DRY RUN ==="))
    self.stdout.write("")

    if config_path:
        self.stdout.write(f"Pipeline Config: {config_path}")
    else:
        self.stdout.write(f"Pipeline Definition: {definition.name}")

    self.stdout.write(f"Source: {options['source']}")
    self.stdout.write(f"Environment: {options['environment']}")
    self.stdout.write("")

    nodes = definition.get_nodes()
    self.stdout.write(f"Nodes ({len(nodes)}):")

    for i, node in enumerate(nodes, 1):
        node_id = node.get("id", f"node_{i}")
        node_type = node.get("type", "unknown")
        node_config = node.get("config", {})
        next_node = node.get("next")

        self.stdout.write(f"  {i}. [{node_type}] {node_id}")
        if node_config:
            self.stdout.write(f"     Config: {json.dumps(node_config)}")
        if next_node:
            self.stdout.write(f"     → next: {next_node}")
        else:
            self.stdout.write("     → end")
        self.stdout.write("")

    self.stdout.write("Payload:")
    self.stdout.write(json.dumps(payload, indent=2))
    self.stdout.write("")
    self.stdout.write(self.style.SUCCESS("Use without --dry-run to execute"))
```

**Step 2: Update `handle()` to use definition dry-run**

Update the dry-run section in `handle()`:

```python
if options["dry_run"]:
    definition, config_path = self._get_definition(options)
    if definition:
        self._show_definition_dry_run(definition, payload, options, config_path)
    else:
        self._show_dry_run(payload, options)
    return
```

**Step 3: Move definition loading before payload**

Reorder in `handle()` so definition is available for dry-run:

```python
def handle(self, *args, **options):
    # Validate mutually exclusive definition options
    if options.get("definition") and options.get("config"):
        raise CommandError("Cannot specify both --definition and --config")

    # Load definition (if specified)
    definition, config_path = self._get_definition(options)

    # Build payload
    payload = self._get_payload(options, definition)

    if options["dry_run"]:
        if definition:
            self._show_definition_dry_run(definition, payload, options, config_path)
        else:
            self._show_dry_run(payload, options)
        return

    # ... rest of handle()
```

**Step 4: Update `_get_payload()` signature**

Update to accept optional definition:

```python
def _get_payload(self, options, definition: "PipelineDefinition | None" = None) -> dict:
```

And update the validation at the end:

```python
elif options["checks_only"]:
    inner_payload = {}
elif definition:
    # Definition-based pipelines can run without explicit payload
    inner_payload = {}
else:
    raise CommandError("Must specify --sample, --payload, --file, --checks-only, --definition, or --config")
```

**Step 5: Run dry-run test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_definition_dry_run -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: implement definition dry-run display"
```

---

## Task 7: Add Tests for Definition Execution

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write failing test for definition execution**

```python
@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
def test_run_pipeline_with_definition(self, mock_execute):
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="test-exec-pipeline",
        config={
            "version": "1.0",
            "nodes": [
                {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
            ]
        }
    )

    mock_execute.return_value = {
        "trace_id": "trace-456",
        "run_id": "run-456",
        "definition": "test-exec-pipeline",
        "definition_version": 1,
        "status": "completed",
        "executed_nodes": ["notify"],
        "skipped_nodes": [],
        "node_results": {
            "notify": {"node_id": "notify", "node_type": "notify", "duration_ms": 50}
        },
        "duration_ms": 100.0,
        "error": None,
    }

    out = io.StringIO()
    call_command("run_pipeline", "--definition", "test-exec-pipeline", stdout=out)
    output = out.getvalue()
    self.assertIn("PIPELINE RESULT", output)
    self.assertIn("Definition: test-exec-pipeline", output)
    self.assertIn("completed", output.lower())
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_with_definition -v`
Expected: FAIL

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add failing test for definition execution"
```

---

## Task 8: Implement Definition Execution

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`

**Step 1: Add definition execution path in `handle()`**

Replace the orchestrator section in `handle()`:

```python
# Run pipeline
self.stdout.write(self.style.NOTICE("Starting pipeline..."))
self.stdout.write(f"  Source: {options['source']}")
self.stdout.write(f"  Environment: {options['environment']}")
if definition:
    self.stdout.write(f"  Definition: {definition.name}")
self.stdout.write("")

try:
    if definition:
        # Definition-based execution
        orchestrator = DefinitionBasedOrchestrator(definition)

        # Validate before execution
        errors = orchestrator.validate()
        if errors:
            raise CommandError(
                "Pipeline definition invalid:\n  - " + "\n  - ".join(errors)
            )

        result = orchestrator.execute(
            payload=payload.get("payload", {}),
            source=options["source"],
            trace_id=options.get("trace_id"),
            environment=options["environment"],
        )

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, default=str))
        else:
            self._display_definition_result(result, definition, config_path)
    else:
        # Hardcoded pipeline execution
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload=payload,
            source=options["source"],
            trace_id=options.get("trace_id"),
            environment=options["environment"],
        )

        if options["json"]:
            self.stdout.write(json.dumps(result.to_dict(), indent=2, default=str))
        else:
            self._display_result(result)

except CommandError:
    raise
except Exception as e:
    raise CommandError(f"Pipeline failed: {e}")
```

**Step 2: Make `config_path` available in execution section**

Ensure `config_path` is defined at the class level or passed through. It's already available from `_get_definition()` call earlier.

**Step 3: Run test (will still fail - missing display method)**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_with_definition -v`
Expected: FAIL (AttributeError: _display_definition_result)

**Step 4: Commit partial implementation**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: add definition-based execution path"
```

---

## Task 9: Implement Definition Result Display

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`

**Step 1: Add `_display_definition_result()` method**

Add after `_display_result()` method:

```python
def _display_definition_result(
    self,
    result: dict,
    definition: "PipelineDefinition",
    config_path: str | None = None,
):
    """Display definition-based pipeline result in human-readable format."""
    self.stdout.write("")
    self.stdout.write("=" * 60)
    self.stdout.write(self.style.HTTP_INFO("PIPELINE RESULT"))
    self.stdout.write("=" * 60)
    self.stdout.write("")

    # Overall status
    status = result.get("status", "unknown")
    if status == "completed":
        self.stdout.write(self.style.SUCCESS(f"Status: {status}"))
    else:
        self.stdout.write(self.style.ERROR(f"Status: {status}"))

    if config_path:
        self.stdout.write(f"Config: {config_path}")
    else:
        self.stdout.write(f"Definition: {result.get('definition', definition.name)}")

    self.stdout.write(f"Trace ID: {result.get('trace_id', 'N/A')}")
    self.stdout.write(f"Run ID: {result.get('run_id', 'N/A')}")
    self.stdout.write(f"Duration: {result.get('duration_ms', 0):.2f}ms")
    self.stdout.write("")

    # Node results
    node_results = result.get("node_results", {})
    nodes = definition.get_nodes()

    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")

        self.stdout.write(f"--- {node_type} ({node_id}) ---")

        if node_id in result.get("skipped_nodes", []):
            self.stdout.write(self.style.WARNING("  (skipped)"))
        elif node_id in node_results:
            node_result = node_results[node_id]

            # Show key info based on node type
            if node_type == "context":
                self.stdout.write(f"  Checks run: {node_result.get('checks_run', 'N/A')}")
            elif node_type == "intelligence":
                summary = node_result.get("summary", node_result.get("output", {}).get("summary", "N/A"))
                if isinstance(summary, str) and len(summary) > 100:
                    summary = summary[:100] + "..."
                self.stdout.write(f"  Summary: {summary}")
                provider = node_result.get("provider", node_result.get("output", {}).get("provider"))
                if provider:
                    self.stdout.write(f"  Provider: {provider}")
            elif node_type == "notify":
                driver = node.get("config", {}).get("driver", "unknown")
                self.stdout.write(f"  Driver: {driver}")
                self.stdout.write(f"  Channels attempted: {node_result.get('channels_attempted', 'N/A')}")
                self.stdout.write(f"  Succeeded: {node_result.get('channels_succeeded', 'N/A')}")
            elif node_type == "ingest":
                self.stdout.write(f"  Incident ID: {node_result.get('incident_id', 'N/A')}")
                self.stdout.write(f"  Alerts created: {node_result.get('alerts_created', 'N/A')}")

            # Show errors if any
            errors = node_result.get("errors", [])
            if errors:
                self.stdout.write(self.style.ERROR(f"  Errors: {errors}"))

            duration = node_result.get("duration_ms", 0)
            self.stdout.write(f"  Duration: {duration:.2f}ms")
        else:
            self.stdout.write(self.style.WARNING("  (not executed)"))

        self.stdout.write("")

    # Final summary
    if status == "completed":
        self.stdout.write(self.style.SUCCESS("✓ Pipeline completed successfully"))
    else:
        self.stdout.write(self.style.ERROR(f"✗ Pipeline failed: {status}"))
        error = result.get("error")
        if error:
            self.stdout.write(self.style.ERROR(f"  {error}"))
```

**Step 2: Run execution test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_with_definition -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "feat: implement definition result display"
```

---

## Task 10: Add Test for Config File Execution

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write test for config file execution**

```python
@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
def test_run_pipeline_with_config_file(self, mock_execute):
    import tempfile
    import os

    config = {
        "version": "1.0",
        "nodes": [
            {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    try:
        mock_execute.return_value = {
            "trace_id": "trace-789",
            "run_id": "run-789",
            "definition": f"__adhoc__{config_path}",
            "definition_version": 1,
            "status": "completed",
            "executed_nodes": ["notify"],
            "skipped_nodes": [],
            "node_results": {},
            "duration_ms": 50.0,
            "error": None,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--config", config_path, stdout=out)
        output = out.getvalue()
        self.assertIn("PIPELINE RESULT", output)
        self.assertIn("completed", output.lower())
    finally:
        os.unlink(config_path)
```

**Step 2: Run test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_with_config_file -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add test for config file execution"
```

---

## Task 11: Add Test for Invalid Config File

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write test for invalid JSON in config file**

```python
def test_run_pipeline_invalid_config_json(self):
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{invalid json content")
        config_path = f.name

    try:
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--config", config_path, stdout=out)
        self.assertIn("Invalid JSON in config file", str(ctx.exception))
    finally:
        os.unlink(config_path)
```

**Step 2: Run test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_invalid_config_json -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add test for invalid config file JSON"
```

---

## Task 12: Add Test for Definition Validation Errors

**Files:**
- Modify: `apps/orchestration/tests/test_run_pipeline_command.py`

**Step 1: Write test for validation errors**

```python
def test_run_pipeline_definition_validation_error(self):
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="invalid-pipeline",
        config={
            # Missing version and has invalid node type
            "nodes": [
                {"id": "bad", "type": "nonexistent_type"},
            ]
        }
    )

    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command("run_pipeline", "--definition", "invalid-pipeline", stdout=out)
    self.assertIn("Pipeline definition invalid", str(ctx.exception))
```

**Step 2: Run test**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_definition_validation_error -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/orchestration/tests/test_run_pipeline_command.py
git commit -m "test: add test for definition validation errors"
```

---

## Task 13: Update Command Docstring

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py:1-22`

**Step 1: Update module docstring**

Replace the docstring at the top of the file:

```python
"""
Management command to run the pipeline end-to-end.

Usage:
    # Run with sample alert payload (hardcoded pipeline)
    python manage.py run_pipeline --sample

    # Run with custom JSON payload
    python manage.py run_pipeline --payload '{"alerts": [...]}'

    # Run with payload from file
    python manage.py run_pipeline --file alert.json

    # Run with specific source
    python manage.py run_pipeline --sample --source grafana

    # Run checks only (no alert ingestion)
    python manage.py run_pipeline --checks-only

    # Dry run (show what would happen)
    python manage.py run_pipeline --sample --dry-run

    # Run a pipeline definition from database
    python manage.py run_pipeline --definition my-pipeline

    # Run a pipeline definition with payload
    python manage.py run_pipeline --definition my-pipeline --payload '{"server": "web-01"}'

    # Run from a JSON config file
    python manage.py run_pipeline --config ./pipelines/custom.json

    # Dry run a definition
    python manage.py run_pipeline --definition my-pipeline --dry-run
"""
```

**Step 2: Update help text**

Update the `help` attribute on the Command class:

```python
help = "Run a pipeline: hardcoded (alerts → checkers → intelligence → notify) or definition-based"
```

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/run_pipeline.py
git commit -m "docs: update run_pipeline command docstring and help"
```

---

## Task 14: Run Full Test Suite

**Step 1: Run all run_pipeline tests**

Run: `pytest apps/orchestration/tests/test_run_pipeline_command.py -v`
Expected: All tests PASS

**Step 2: Run full orchestration tests**

Run: `pytest apps/orchestration/ -v`
Expected: All tests PASS

**Step 3: Final commit if any cleanup needed**

```bash
git status
# If clean, no commit needed
```

---

## Task 15: Manual Verification

**Step 1: Test dry-run with sample definition**

Create a test definition in Django shell:

```bash
python manage.py shell -c "
from apps.orchestration.models import PipelineDefinition
PipelineDefinition.objects.get_or_create(
    name='cli-test',
    defaults={
        'config': {
            'version': '1.0',
            'nodes': [
                {'id': 'ctx', 'type': 'context', 'config': {'include': ['cpu']}, 'next': 'notify'},
                {'id': 'notify', 'type': 'notify', 'config': {'driver': 'generic'}},
            ]
        }
    }
)
print('Created cli-test definition')
"
```

**Step 2: Test dry-run**

Run: `python manage.py run_pipeline --definition cli-test --dry-run`

Verify output shows:
- Pipeline Definition: cli-test
- Nodes (2):
- [context] ctx
- [notify] notify

**Step 3: Test with JSON config file**

Create a test config file and run dry-run:

```bash
echo '{"version": "1.0", "nodes": [{"id": "n", "type": "notify", "config": {"driver": "generic"}}]}' > /tmp/test-pipeline.json
python manage.py run_pipeline --config /tmp/test-pipeline.json --dry-run
```
