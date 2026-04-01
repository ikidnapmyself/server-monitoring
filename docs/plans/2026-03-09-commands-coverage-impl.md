---
title: "2026-03-09 Management Commands Coverage Implementation"
parent: Plans
nav_order: 79739690
---

# Management Commands Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring `monitor_pipeline.py` and `run_pipeline.py` to 100% branch coverage.

**Architecture:** Add targeted tests to existing test files. No source changes needed — both files are clean and use modern syntax. All tests mock DB/orchestrator to stay fast and isolated.

**Tech Stack:** Django TestCase, `call_command`, `unittest.mock`

---

### Task 1: monitor_pipeline — list_runs with results

**Files:**
- Modify: `apps/orchestration/_tests/test_monitor_pipeline.py`

**Step 1: Write test**

```python
def test_list_runs_shows_table(self):
    """list_runs displays pipeline runs in table format."""
    PipelineRun.objects.create(
        run_id="run-001",
        trace_id="trace-001",
        source="test",
        status="completed",
    )
    out = StringIO()
    call_command("monitor_pipeline", stdout=out)
    output = out.getvalue()
    assert "run-001" in output
    assert "trace-001" in output
    assert "completed" in output
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_monitor_pipeline.py -v`

**Step 3: Commit** — defer until Task 5 (batch commit)

---

### Task 2: monitor_pipeline — list_runs empty + filtered

**Files:**
- Modify: `apps/orchestration/_tests/test_monitor_pipeline.py`

**Step 1: Write tests**

```python
def test_list_runs_empty(self):
    """list_runs shows warning when no runs exist."""
    out = StringIO()
    call_command("monitor_pipeline", stdout=out)
    output = out.getvalue()
    assert "No pipeline runs found" in output

def test_list_runs_filtered_by_status(self):
    """list_runs filters by --status flag."""
    PipelineRun.objects.create(
        run_id="run-ok", trace_id="t", source="test", status="completed",
    )
    PipelineRun.objects.create(
        run_id="run-fail", trace_id="t", source="test", status="failed",
    )
    out = StringIO()
    call_command("monitor_pipeline", "--status", "failed", stdout=out)
    output = out.getvalue()
    assert "run-fail" in output
    assert "run-ok" not in output
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_monitor_pipeline.py -v`

---

### Task 3: monitor_pipeline — show_run_details not found

**Files:**
- Modify: `apps/orchestration/_tests/test_monitor_pipeline.py`

**Step 1: Write test**

```python
def test_show_run_details_not_found(self):
    """show_run_details shows error for nonexistent run_id."""
    out = StringIO()
    call_command("monitor_pipeline", "--run-id", "nonexistent", stdout=out)
    output = out.getvalue()
    assert "Pipeline run not found" in output
```

**Step 2: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_monitor_pipeline.py -v`

---

### Task 4: monitor_pipeline — show_run_details with error and stages

**Files:**
- Modify: `apps/orchestration/_tests/test_monitor_pipeline.py`

**Step 1: Write test**

```python
def test_show_run_details_with_error_and_stages(self):
    """show_run_details displays last_error_message and stage errors."""
    run = PipelineRun.objects.create(
        run_id="run-err",
        trace_id="trace-err",
        source="test",
        status="failed",
        last_error_message="Pipeline timeout",
    )
    run.stage_executions.create(
        stage="ingest",
        status="completed",
        attempt=1,
        duration_ms=10.0,
    )
    run.stage_executions.create(
        stage="analyze",
        status="failed",
        attempt=1,
        duration_ms=5.0,
        error_message="Provider unavailable",
    )
    out = StringIO()
    call_command("monitor_pipeline", "--run-id", "run-err", stdout=out)
    output = out.getvalue()
    assert "Pipeline timeout" in output
    assert "Provider unavailable" in output
    assert "ingest" in output
    assert "analyze" in output
```

**Step 2: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_monitor_pipeline.py -v`

---

### Task 5: Verify monitor_pipeline coverage and commit

**Step 1: Run coverage**

```bash
uv run coverage run -m pytest apps/orchestration/_tests/test_monitor_pipeline.py -q
uv run coverage report --include="apps/orchestration/management/commands/monitor_pipeline.py" --show-missing
```

Expected: 100%

**Step 2: Commit**

```bash
git add apps/orchestration/_tests/test_monitor_pipeline.py
git commit -m "test: bring monitor_pipeline.py to 100% branch coverage"
```

---

### Task 6: run_pipeline — generic exception wrapping

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write test**

Covers lines 179-180: non-CommandError exception in handle() gets wrapped.

```python
@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_generic_exception_wrapped_as_command_error(self, mock_orchestrator):
    """Non-CommandError exceptions are wrapped in CommandError."""
    mock_orchestrator.return_value.run_pipeline.side_effect = RuntimeError("unexpected")

    out = io.StringIO()
    with self.assertRaises(CommandError) as ctx:
        call_command("run_pipeline", "--sample", stdout=out)
    self.assertIn("Pipeline failed", str(ctx.exception))
```

**Step 2: Run test**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_generic_exception_wrapped_as_command_error -v`

---

### Task 7: run_pipeline — file payload with invalid JSON

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write test**

Covers lines 196-197: `--file` with invalid JSON content.

```python
def test_run_pipeline_file_invalid_json(self):
    """--file with invalid JSON raises CommandError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{not valid json")
        path = f.name
    try:
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--file", path, stdout=io.StringIO())
        self.assertIn("Invalid JSON in file", str(ctx.exception))
    finally:
        os.unlink(path)
```

**Step 2: Run test**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_run_pipeline_file_invalid_json -v`

---

### Task 8: run_pipeline — stage_result with to_dict(), NOTIFY stage, stage errors

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write test**

Covers line 341 (`to_dict()` path), lines 424→432 (NOTIFY branch), line 434 (errors non-empty).

```python
@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_display_result_notify_stage_with_to_dict_and_errors(self, mock_orchestrator):
    """NOTIFY stage with to_dict() objects and errors displayed."""
    mock_result = mock.Mock()
    mock_result.status = "COMPLETED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 10

    # Use mock with to_dict() for ingest (covers line 341)
    ingest_stage = mock.Mock()
    ingest_stage.to_dict.return_value = {
        "incident_id": 1, "alerts_created": 1, "severity": "warning", "duration_ms": 5,
    }
    mock_result.ingest = ingest_stage
    mock_result.check = None
    mock_result.analyze = None
    # NOTIFY stage as dict with errors (covers 424→432, 434)
    mock_result.notify = {
        "channels_attempted": 1,
        "channels_succeeded": 0,
        "channels_failed": 1,
        "errors": ["Channel failed"],
        "duration_ms": 5,
    }
    mock_result.errors = []
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command("run_pipeline", "--sample", stdout=out)
    output = out.getvalue()
    self.assertIn("Channels attempted: 1", output)
    self.assertIn("Errors:", output)
```

**Step 2: Run test**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_display_result_notify_stage_with_to_dict_and_errors -v`

---

### Task 9: run_pipeline — final_error with stack_trace

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write test**

Covers lines 455-458: `final_error` object with `stack_trace` attribute.

```python
@mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
def test_display_result_failed_with_stack_trace(self, mock_orchestrator):
    """Failed pipeline with final_error containing stack_trace."""
    mock_result = mock.Mock()
    mock_result.status = "FAILED"
    mock_result.trace_id = "t"
    mock_result.run_id = "r"
    mock_result.total_duration_ms = 10
    mock_result.ingest = None
    mock_result.check = None
    mock_result.analyze = None
    mock_result.notify = None
    mock_result.errors = ["error"]

    final_error = mock.Mock()
    final_error.error_type = "RuntimeError"
    final_error.message = "something broke"
    final_error.stack_trace = "Traceback:\n  File ..."
    mock_result.final_error = final_error
    mock_orchestrator.return_value.run_pipeline.return_value = mock_result

    out = io.StringIO()
    call_command("run_pipeline", "--sample", stdout=out)
    output = out.getvalue()
    self.assertIn("RuntimeError", output)
    self.assertIn("something broke", output)
    self.assertIn("Traceback:", output)
```

**Step 2: Run test**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py::RunPipelineCommandTest::test_display_result_failed_with_stack_trace -v`

---

### Task 10: run_pipeline — definition display edge cases

**Files:**
- Modify: `apps/orchestration/_tests/test_run_pipeline_command.py`

**Step 1: Write tests**

Covers: 359→361 (empty node config in dry run), 522 (summary truncation), 525→551 (provider None), 546→551 (ingest node type — already tested but branch partial), 569 (error string in failed result).

```python
def test_definition_dry_run_node_without_config(self):
    """Dry run skips config line when node has empty config."""
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="test-no-config",
        config={
            "version": "1.0",
            "nodes": [
                {"id": "ctx", "type": "context", "config": {}, "next": "notify"},
                {"id": "notify", "type": "notify", "config": {}},
            ],
        },
    )
    out = io.StringIO()
    call_command("run_pipeline", "--definition", "test-no-config", "--dry-run", stdout=out)
    output = out.getvalue()
    self.assertIn("[context] ctx", output)
    # Empty config → no "Config:" line for that node
    self.assertNotIn("Config: {}", output)

@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
def test_display_definition_intelligence_long_summary_no_provider(self, mock_execute, mock_validate):
    """Intelligence node with long summary truncated, no provider shown."""
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="test-long-summary",
        config={
            "version": "1.0",
            "nodes": [
                {"id": "analyze", "type": "intelligence", "config": {"provider": "local"}},
            ],
        },
    )
    mock_validate.return_value = []
    mock_execute.return_value = {
        "trace_id": "t", "run_id": "r",
        "definition": "test-long-summary",
        "status": "completed",
        "executed_nodes": ["analyze"],
        "skipped_nodes": [],
        "node_results": {
            "analyze": {
                "node_id": "analyze", "node_type": "intelligence",
                "output": {"summary": "A" * 150},
                "errors": [], "duration_ms": 10.0,
            },
        },
        "duration_ms": 20.0,
    }
    out = io.StringIO()
    call_command("run_pipeline", "--definition", "test-long-summary", stdout=out)
    output = out.getvalue()
    self.assertIn("..." , output)
    self.assertNotIn("Provider:", output)

@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
@mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
def test_display_definition_failed_with_error_message(self, mock_execute, mock_validate):
    """Failed definition result shows error message."""
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="test-fail-err",
        config={
            "version": "1.0",
            "nodes": [
                {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
            ],
        },
    )
    mock_validate.return_value = []
    mock_execute.return_value = {
        "trace_id": "t", "run_id": "r",
        "definition": "test-fail-err",
        "status": "failed",
        "executed_nodes": ["notify"],
        "skipped_nodes": [],
        "node_results": {},
        "duration_ms": 10.0,
        "error": "Node notify raised an exception",
    }
    out = io.StringIO()
    call_command("run_pipeline", "--definition", "test-fail-err", stdout=out)
    output = out.getvalue()
    self.assertIn("Pipeline failed", output)
    self.assertIn("Node notify raised an exception", output)
```

**Step 2: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py -v`

---

### Task 11: Verify run_pipeline coverage and commit

**Step 1: Run coverage**

```bash
uv run coverage run -m pytest apps/orchestration/_tests/test_run_pipeline_command.py -q
uv run coverage report --include="apps/orchestration/management/commands/run_pipeline.py" --show-missing
```

Expected: 100%

**Step 2: Commit**

```bash
git add apps/orchestration/_tests/test_run_pipeline_command.py
git commit -m "test: bring run_pipeline.py to 100% branch coverage"
```

---

### Task 12: Final verification

**Step 1: Run full coverage for all commands**

```bash
uv run coverage run -m pytest -q
uv run coverage report --include="apps/orchestration/management/commands/*.py" --show-missing
```

Expected: 100% on all files.

**Step 2: Run pre-commit**

```bash
uv run pre-commit run --all-files
```

Expected: All pass.