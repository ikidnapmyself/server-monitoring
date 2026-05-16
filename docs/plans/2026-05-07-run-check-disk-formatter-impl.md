---
title: "2026-05-07 run_check Disk Formatter Implementation Plan"
parent: Plans
---

# `run_check` Disk Formatter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `run_check` render disk-checker metrics readably (subtotals, items, trailers) by extracting the existing rendering logic from `check_health` into a shared helper that both commands call.

**Architecture:** New module `apps/checkers/management/commands/_metrics_format.py` exports `write_metrics(stdout, metrics, indent)`. `check_health.Command._output_metrics` becomes a thin wrapper. `run_check.Command._output_text` replaces its naive metrics loop with a call to the helper, keeping its existing `"  Metrics:"` wrapper line and 4-space indent. The helper's algorithm is byte-identical to today's `_output_metrics` — only the call surface changes.

**Tech Stack:** Python 3, Django management commands, `unittest.TestCase` via `django.test.TestCase`, `pytest` runner, `coverage` for branch coverage.

**Design doc:** `docs/plans/2026-05-07-run-check-disk-formatter-design.md`

**Branch:** `fix/run-check-disk-formatter` (already created from `fix/disk-checker-output-reconciliation`'s HEAD `6c7d6d4`). This is **stacked on PR #132**. Do not branch from `main`.

**Two commits planned:**

- Commit A — Refactor only (no behavior change). Adds the helper, migrates format tests into the helper's unit file, switches `check_health` to delegate. All existing tests pass before and after.
- Commit B — Behavior change. Switches `run_check` to use the helper, adds wiring tests for `run_check`.

This split makes git-bisect easy: if `check_health` regresses, Commit A is suspect; if `run_check` regresses, Commit B.

---

## Background — what is changing

Currently:

- `check_health._output_metrics` (lines 194-275) renders disk metrics readably — per-section subtotals, full-largest-section, byte-accurate trailer, recommendations, flat keys, nested dicts.
- `run_check._output_text` (lines 165-175) dumps any non-dict metric value via `f"{key}: {value}"`. For disk checkers, `space_hogs` becomes a `repr()` of a list-of-dicts — unusable.

After this PR:

- `_metrics_format.write_metrics(stdout, metrics, indent)` holds the rendering logic.
- `check_health._output_metrics` calls it with `indent="       "`.
- `run_check._output_text` calls it with `indent="    "` after writing `"  Metrics:"`.

Visible run_check change for non-disk checkers — flat keys now have underscores rendered as spaces (matching `check_health`):

- Old: `    cpu_percent: 15.5`
- New: `    cpu percent: 15.5`

User confirmed this consistency is desired.

---

## Commit A — Refactor (no behavior change)

### Task 1: Create the helper module

Copy-paste the body of `check_health._output_metrics` into a module-level function in a new file. Replace `self.stdout` with the `stdout` parameter and lift `indent = "       "` to a parameter. No algorithmic change.

**Files:**
- Create: `apps/checkers/management/commands/_metrics_format.py`

**Step 1: Read current `_output_metrics`**

Run: `sed -n '194,275p' apps/checkers/management/commands/check_health.py`
Note exact indentation and the bodies of all four blocks (disk sections, total/recommendations, flat keys, nested dicts).

**Step 2: Create the helper**

Write `apps/checkers/management/commands/_metrics_format.py`:

```python
"""Shared metrics rendering for the check_health and run_check commands."""


def write_metrics(stdout, metrics: dict, indent: str) -> None:
    """Render checker metrics to stdout with the given indent.

    Disk checkers' space_hogs / old_files / large_files lists are
    rendered with per-section subtotals, full output for the largest
    section when 2+ are non-empty, and a byte-accurate trailer on
    truncated sections so the printed values reconcile against the
    grand total.
    """
    # Disk analysis checkers: space_hogs, old_files, large_files, recommendations.
    # Display rule: when 2+ sections are non-empty, the section with the largest
    # subtotal is shown in full so the user can see where the weight is.
    # Other sections (and the single-section case) keep the 10-item cap with a
    # trailer that includes the omitted byte weight, so the printed values
    # always reconcile against the grand total.
    sections = []
    for key in ("space_hogs", "old_files", "large_files"):
        items = metrics.get(key)
        if items:
            subtotal = sum(item["size_mb"] for item in items)
            sections.append((key, items, subtotal))

    largest_key = None
    if len(sections) >= 2:
        largest_key = max(sections, key=lambda s: s[2])[0]

    cap = 10
    for key, items, subtotal in sections:
        label = key.replace("_", " ").title()
        show_all = key == largest_key
        shown = items if show_all else items[:cap]
        count_note = "all shown" if show_all or len(items) <= cap else f"top {cap} shown"
        stdout.write(
            f"{indent}{label}: {subtotal:.1f} MB ({len(items)} items, {count_note})"
        )
        for item in shown:
            size = f"{item['size_mb']:.1f} MB"
            extra = f" ({item['age_days']}d old)" if "age_days" in item else ""
            stdout.write(f"{indent}  - {item['path']}  {size}{extra}")
        if not show_all and len(items) > cap:
            omitted_weight = sum(it["size_mb"] for it in items[cap:])
            stdout.write(
                f"{indent}  ... and {len(items) - cap} more  ({omitted_weight:.1f} MB)"
            )

    total = metrics.get("total_recoverable_mb")
    if total is not None:
        stdout.write(f"{indent}Total recoverable: {total:.1f} MB")

    recs = metrics.get("recommendations")
    if recs:
        stdout.write(f"{indent}Recommendations:")
        for rec in recs:
            stdout.write(f"{indent}  - {rec}")

    # Standard checkers: flat key-value pairs (percent, paths, etc.)
    skip = {
        "space_hogs",
        "old_files",
        "large_files",
        "total_recoverable_mb",
        "recommendations",
        "platform",
    }
    flat = {
        k: v for k, v in metrics.items() if k not in skip and not isinstance(v, (list, dict))
    }
    for key, value in flat.items():
        label = key.replace("_", " ")
        if isinstance(value, float):
            stdout.write(f"{indent}{label}: {value:.1f}")
        else:
            stdout.write(f"{indent}{label}: {value}")

    # Nested dicts (e.g. disk checker's per-path breakdown)
    nested = {k: v for k, v in metrics.items() if k not in skip and isinstance(v, dict)}
    for key, sub in nested.items():
        stdout.write(f"{indent}{key}:")
        for sub_key, sub_val in sub.items():
            if isinstance(sub_val, dict):
                parts = ", ".join(f"{k}: {v}" for k, v in sub_val.items())
                stdout.write(f"{indent}  {sub_key}: {parts}")
            elif isinstance(sub_val, float):
                stdout.write(f"{indent}  {sub_key}: {sub_val:.1f}")
            else:
                stdout.write(f"{indent}  {sub_key}: {sub_val}")
```

**Step 3: Verify import works**

Run: `uv run python -c "from apps.checkers.management.commands._metrics_format import write_metrics; print(write_metrics.__doc__)"`
Expected: prints the docstring.

---

### Task 2: Create the unit test file

13 tests calling `write_metrics(out, metrics, indent=...)` directly. Most are migrations of existing `CheckHealthCommandTests` tests with the same assertion strings.

**Files:**
- Create: `apps/checkers/_tests/test_metrics_format.py`

**Step 1: Write the test file**

```python
"""Unit tests for the shared metrics formatter."""

from io import StringIO

from django.test import SimpleTestCase

from apps.checkers.management.commands._metrics_format import write_metrics


class WriteMetricsTests(SimpleTestCase):
    """Direct unit tests for write_metrics."""

    INDENT = "       "  # 7 spaces, matching check_health's indent

    def _render(self, metrics, indent=None):
        out = StringIO()
        write_metrics(out, metrics, indent=indent if indent is not None else self.INDENT)
        return out.getvalue()

    def test_no_disk_sections(self):
        output = self._render({"cpu_percent": 12.5})
        self.assertNotIn("Space Hogs", output)
        self.assertNotIn("Old Files", output)
        self.assertNotIn("Large Files", output)
        self.assertIn("cpu percent: 12.5", output)

    def test_section_all_shown_when_under_cap(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 10.0} for i in range(5)]
        output = self._render({"space_hogs": items})
        self.assertIn("Space Hogs: 50.0 MB (5 items, all shown)", output)
        self.assertNotIn("... and", output)

    def test_section_truncated_with_trailer(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        output = self._render({"space_hogs": items})
        self.assertIn("Space Hogs: 1206.0 MB (12 items, top 10 shown)", output)
        self.assertIn("/tmp/file0", output)
        self.assertIn("100.5 MB", output)
        self.assertIn("30d old", output)
        self.assertIn("... and 2 more  (201.0 MB)", output)

    def test_largest_section_shown_in_full(self):
        space_hogs = [{"path": f"/tmp/s{i}", "size_mb": 5.0} for i in range(12)]
        old_files = [{"path": f"/tmp/o{i}", "size_mb": 50.0, "age_days": 7} for i in range(12)]
        output = self._render({"space_hogs": space_hogs, "old_files": old_files})
        self.assertIn("Space Hogs: 60.0 MB (12 items, top 10 shown)", output)
        self.assertIn("... and 2 more  (10.0 MB)", output)
        self.assertIn("Old Files: 600.0 MB (12 items, all shown)", output)
        self.assertIn("/tmp/o11", output)

    def test_three_sections_largest_wins(self):
        space_hogs = [{"path": f"/v/s{i}", "size_mb": 1.0} for i in range(11)]
        old_files = [{"path": f"/v/o{i}", "size_mb": 2.0, "age_days": 5} for i in range(11)]
        large_files = [{"path": f"/h/l{i}", "size_mb": 100.0} for i in range(11)]
        output = self._render({
            "space_hogs": space_hogs,
            "old_files": old_files,
            "large_files": large_files,
        })
        self.assertIn("Space Hogs: 11.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Old Files: 22.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Large Files: 1100.0 MB (11 items, all shown)", output)
        self.assertIn("/h/l10", output)
        self.assertNotIn("/v/s10", output)
        self.assertNotIn("/v/o10", output)

    def test_old_files_section_with_age_annotation(self):
        items = [{"path": "/tmp/old", "size_mb": 50.0, "age_days": 30}]
        output = self._render({"old_files": items})
        self.assertIn("Old Files: 50.0 MB (1 items, all shown)", output)
        self.assertIn("/tmp/old", output)
        self.assertIn("50.0 MB", output)
        self.assertIn("(30d old)", output)

    def test_large_files_section(self):
        items = [{"path": "/tmp/large", "size_mb": 200.0}]
        output = self._render({"large_files": items})
        self.assertIn("Large Files: 200.0 MB (1 items, all shown)", output)
        self.assertNotIn("d old", output)

    def test_total_recoverable(self):
        output = self._render({"total_recoverable_mb": 500.0})
        self.assertIn("Total recoverable: 500.0 MB", output)

    def test_recommendations(self):
        output = self._render({"recommendations": ["clean /tmp"]})
        self.assertIn("Recommendations:", output)
        self.assertIn("- clean /tmp", output)

    def test_nested_dict(self):
        output = self._render({
            "paths": {
                "/": {"total": 100, "used": 50},
                "free_pct": 50.0,
                "label": "root",
            }
        })
        self.assertIn("paths:", output)
        self.assertIn("/: total: 100, used: 50", output)
        self.assertIn("free_pct: 50.0", output)
        self.assertIn("label: root", output)

    def test_flat_key_underscore_to_space_and_float_format(self):
        output = self._render({"cpu_percent": 95.5})
        self.assertIn("cpu percent: 95.5", output)

    def test_flat_key_integer_value(self):
        output = self._render({"count": 42})
        self.assertIn("count: 42", output)

    def test_indent_parameter(self):
        items = [{"path": "/tmp/file0", "size_mb": 50.0}]
        output = self._render({"space_hogs": items}, indent="    ")
        # Header indented 4 spaces; bullet indented 6 (header + 2)
        self.assertIn("    Space Hogs: 50.0 MB (1 items, all shown)", output)
        self.assertIn("      - /tmp/file0  50.0 MB", output)
        # Should NOT use the default 7-space indent
        self.assertNotIn("       Space Hogs", output)

    def test_platform_key_is_skipped(self):
        output = self._render({"platform": "darwin", "cpu_percent": 12.5})
        self.assertNotIn("platform", output)
        self.assertIn("cpu percent: 12.5", output)

    def test_empty_metrics(self):
        output = self._render({})
        self.assertEqual(output, "")
```

That's 14 tests covering: every disk-section branch, age annotation, total, recommendations, nested dict, flat keys (float/int/skip), indent parameterization, and the empty-input edge case.

**Step 2: Run the new tests**

Run: `uv run pytest apps/checkers/_tests/test_metrics_format.py -v`
Expected: all 14 PASS.

If any fail, the helper has a copy-paste bug — diff against `check_health._output_metrics` to find what was lost.

---

### Task 3: Switch `check_health` to delegate

`check_health.Command._output_metrics` becomes a one-liner.

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py:194-275`

**Step 1: Add the import**

Near the top of the file, alongside other imports:

```python
from apps.checkers.management.commands._metrics_format import write_metrics
```

**Step 2: Replace `_output_metrics`**

Replace the entire `_output_metrics` method (currently lines 194-275, ~80 lines) with:

```python
    def _output_metrics(self, metrics: dict):
        """Print key metrics below the checker result line."""
        write_metrics(self.stdout, metrics, indent="       ")
```

**Step 3: Run all `check_health` tests**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests -v`
Expected: every test still passes — output is byte-identical because the helper has byte-identical logic.

If any fails, the cause is either a missed branch in the helper or an unintended edit to surrounding code. Investigate before proceeding.

---

### Task 4: Migrate format-shape tests out of `CheckHealthCommandTests`

The 12 disk/flat/nested format tests in `CheckHealthCommandTests` are now redundant with `WriteMetricsTests`. Remove them and replace with one wiring smoke test.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py`

**Step 1: Identify the tests to remove**

These methods, all on `CheckHealthCommandTests`:

- `test_metrics_display_float`
- `test_metrics_display_integer`
- `test_metrics_space_hogs`
- `test_metrics_old_files`
- `test_metrics_large_files`
- `test_metrics_total_recoverable_mb`
- `test_metrics_recommendations`
- `test_metrics_nested_dict`
- `test_metrics_section_all_shown_when_under_cap`
- `test_metrics_largest_section_shown_in_full`
- `test_metrics_three_sections_largest_wins`
- `test_metrics_no_disk_sections`

12 tests, ~80 lines. Each goes through `call_command("check_health", ...)` to assert on a substring of the output. Their assertions are all covered by direct calls in `WriteMetricsTests`.

**Step 2: Delete those methods**

Carefully delete each method. Do not delete any other test in the class.

**Step 3: Add one wiring smoke test**

Add this to `CheckHealthCommandTests`, in roughly the same location as the deleted block:

```python
    def test_check_health_uses_metrics_formatter(self):
        """Smoke test: command pipes metrics through write_metrics."""
        items = [{"path": f"/tmp/file{i}", "size_mb": 10.0} for i in range(5)]
        mock_checker = self._make_checker(metrics={"space_hogs": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        # The section header is unique to write_metrics' format. If the
        # command is wired up correctly, this line will appear.
        self.assertIn("Space Hogs: 50.0 MB (5 items, all shown)", output)
```

**Step 4: Run the full test module**

Run: `uv run pytest apps/checkers/_tests/test_commands.py -v`
Expected: all PASS. The test count is lower than before (we removed 12, added 1, net -11), but no failures.

---

### Task 5: Run full checker tests + coverage spot-check + commit Commit A

**Step 1: Run the full checker suite**

Run: `uv run pytest apps/checkers/ -v`
Expected: all PASS. Net test delta: −11 tests in `test_commands.py`, +14 in `test_metrics_format.py`. Final is +3 tests overall.

**Step 2: Spot-check coverage on the helper and the wrapper**

Run:
```
uv run coverage run --branch -m pytest apps/checkers/_tests/test_metrics_format.py apps/checkers/_tests/test_commands.py
uv run coverage report -m --include='apps/checkers/management/commands/_metrics_format.py,apps/checkers/management/commands/check_health.py'
```
Expected: both files at 100 %.

**Step 3: Lint, format, type-check**

Run:
```
uv run black --check apps/checkers/management/commands/_metrics_format.py apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_metrics_format.py apps/checkers/_tests/test_commands.py && uv run ruff check apps/checkers/management/commands/_metrics_format.py apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_metrics_format.py apps/checkers/_tests/test_commands.py && uv run mypy apps/checkers/management/commands/_metrics_format.py apps/checkers/management/commands/check_health.py
```
Expected: clean.

**Step 4: Commit (Commit A — refactor only)**

```bash
git add apps/checkers/management/commands/_metrics_format.py apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_metrics_format.py apps/checkers/_tests/test_commands.py
git commit -m "$(cat <<'EOF'
refactor(checkers): extract write_metrics helper from check_health

The disk-aware metrics renderer added in #132 is also needed by
run_check (currently dumping space_hogs as Python repr — unreadable).
Extract _output_metrics' body into a module-level helper write_metrics
that takes stdout and indent as parameters.

This commit is a pure refactor: check_health output is byte-identical
before and after. The 12 disk/flat/nested format tests in
CheckHealthCommandTests are migrated to a new test_metrics_format.py
that calls write_metrics directly; one wiring smoke test remains in
CheckHealthCommandTests to verify the command delegates to the helper.

Net test delta: -12 / +14, +3 overall.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, run `git status` to confirm clean and `git log --oneline -1` to confirm the commit message.

---

## Commit B — Apply to `run_check` (behavior change)

### Task 6: Switch `run_check._output_text` to use the helper

**Files:**
- Modify: `apps/checkers/management/commands/run_check.py:165-175`

**Step 1: Add the import**

Near the top of the file, alongside the existing imports:

```python
from apps.checkers.management.commands._metrics_format import write_metrics
```

**Step 2: Replace the metrics block**

Find the existing block in `_output_text`:

```python
        # Show key metrics
        if result.metrics and not skipped:
            self.stdout.write("")
            self.stdout.write("  Metrics:")
            for key, value in result.metrics.items():
                if isinstance(value, dict):
                    self.stdout.write(f"    {key}:")
                    for k, v in value.items():
                        self.stdout.write(f"      {k}: {v}")
                else:
                    self.stdout.write(f"    {key}: {value}")
```

Replace with:

```python
        # Show key metrics — disk checkers get readable section headers,
        # subtotals, and trailers via the shared helper.
        if result.metrics and not skipped:
            self.stdout.write("")
            self.stdout.write("  Metrics:")
            write_metrics(self.stdout, result.metrics, indent="    ")
```

The `"  Metrics:"` wrapper line and 4-space body indent are run_check's own chrome. The body content now goes through `write_metrics`.

**Step 3: Run the run_check tests**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::RunCheckCommandTests -v`
Expected: all existing tests still PASS. They mostly assert on call args (not output content). If any unexpectedly fails — for example, one that asserts on `cpu_percent: ...` (old format) — STOP and read it before changing it; the assertion may have been right under the old format and need to be updated to the new format. Report the test name and discuss before editing.

---

### Task 7: Add wiring tests for `run_check`

Add three tests to `RunCheckCommandTests`. They verify the command produces the right chrome (`"  Metrics:"`), routes disk metrics through the helper, and uses the new flat-key format.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py` (add to `RunCheckCommandTests`)

**Step 1: Add the tests**

Place these three tests inside `RunCheckCommandTests`. The class already has a `_make_checker` helper similar to the one in `CheckHealthCommandTests`; reuse it.

```python
    def test_run_check_wraps_metrics_with_label(self):
        """The metrics block is preceded by a 'Metrics:' header line."""
        mock_checker = self._make_checker(metrics={"cpu_percent": 15.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("  Metrics:", output)

    def test_run_check_disk_metrics_use_section_format(self):
        """Disk space_hogs render through the shared helper, not as repr()."""
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        mock_checker = self._make_checker(
            metrics={"space_hogs": items}, checker_name="disk_common"
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"disk_common": mock_checker}, clear=True):
            call_command("run_check", "disk_common", stdout=out)
        output = out.getvalue()
        # Helper produces this exact header; repr-dumping would produce "[{'path': ...}]"
        self.assertIn("Space Hogs: 1206.0 MB (12 items, top 10 shown)", output)
        self.assertIn("... and 2 more  (201.0 MB)", output)
        self.assertNotIn("[{", output)  # No Python list repr leaked into output

    def test_run_check_flat_metric_uses_helper_format(self):
        """Flat keys render with underscore-to-space and float :.1f formatting."""
        mock_checker = self._make_checker(metrics={"cpu_percent": 15.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("cpu percent: 15.5", output)
        self.assertNotIn("cpu_percent:", output)  # old format is gone
```

You may need to confirm `RunCheckCommandTests._make_checker` exists. If it doesn't, add a copy of the one from `CheckHealthCommandTests` (it's small) — same signature, same shape.

**Step 2: Run the run_check tests**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::RunCheckCommandTests -v`
Expected: all PASS, including the three new tests.

**Step 3: Run the full checkers suite**

Run: `uv run pytest apps/checkers/ -v`
Expected: all PASS.

---

### Task 8: Coverage check on `run_check.py`

CLAUDE.md requires 100 % branch coverage.

**Step 1: Run**

```
uv run coverage run --branch -m pytest apps/checkers/_tests/test_commands.py
uv run coverage report -m --include='apps/checkers/management/commands/run_check.py'
```
Expected: 100 %.

**Step 2: If gaps exist**

Investigate which branches aren't covered. Likely candidates: error paths in `handle()` (already covered by existing tests), or the `skipped` guard in `_output_text` (covered by an existing test that hits `Skipped:` message). If a real gap exists, add one focused test before proceeding.

---

### Task 9: Lint, format, type-check

```
uv run black --check apps/checkers/management/commands/run_check.py apps/checkers/_tests/test_commands.py
uv run ruff check apps/checkers/management/commands/run_check.py apps/checkers/_tests/test_commands.py
uv run mypy apps/checkers/management/commands/run_check.py
```
Expected: clean.

---

### Task 10: Live sanity check on this Mac

**Step 1: Run on a real disk checker**

Run: `uv run python manage.py run_check disk_macos`
Expected output structure:

```
[STATUS] disk_macos
  Disk analysis: X.X MB recoverable

  Metrics:
    Space Hogs: X.X MB (N items, ...)
      - /Users/...  X.X MB
      ...
    Old Files: X.X MB (N items, ...)
    Total recoverable: X.X MB
    Recommendations:
      - ...
```

Verify:
- `"  Metrics:"` is present (run_check chrome).
- The body is indented 4 spaces; bullet items are indented 6.
- No Python list repr (`[{'path': ...`) appears anywhere.
- Subtotals + trailer reconcile against `Total recoverable:` (within ±0.5 MB rounding drift).

**Step 2: Run on a non-disk checker**

Run: `uv run python manage.py run_check cpu`
Verify:
- `"cpu percent: X.X"` (with space, not `cpu_percent`) appears.
- Other CPU metrics render with the new format.

**Step 3: Run with --json**

Run: `uv run python manage.py run_check disk_macos --json`
Expected: standard JSON output (the JSON path bypasses `_output_text` entirely). Must not be affected by this change.

If any of these don't match, STOP and report. Don't push.

---

### Task 11: Commit B — `run_check` behavior change

```bash
git add apps/checkers/management/commands/run_check.py apps/checkers/_tests/test_commands.py
git commit -m "$(cat <<'EOF'
fix(checkers): use shared write_metrics in run_check

Previously run_check dumped disk-checker space_hogs/old_files/large_files
via Python's list repr — a single-line wall of dicts. After this change
it routes the entire metrics block through the write_metrics helper
(extracted in the previous commit), so disk checkers get the same
readable section headers, subtotals, and trailers that check_health
already produces.

The command keeps its existing "Metrics:" wrapper line and 4-space
body indent — only the rendering inside it changes.

Visible side effect for non-disk checkers: flat keys now render with
underscores stripped (e.g. "cpu percent: 15.5" instead of
"cpu_percent: 15.5"), matching check_health.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, `git log --oneline f1c0db3..HEAD` should show 5 commits on the branch (3 from #132 + design doc + Commit A + Commit B = 6, depending on stacking).

Wait — the count is:
- 3 from PR #132 (design, impl plan, fix)
- 1 design doc for this PR
- Commit A
- Commit B
- Total: 6 commits ahead of `main`.

---

### Task 12: Push branch and open stacked PR

**Step 1: Push**

```bash
git push -u origin fix/run-check-disk-formatter
```

**Step 2: Open PR with explicit base**

The PR's base must be `fix/disk-checker-output-reconciliation` (the #132 branch), not `main`, because this PR is stacked.

```bash
gh pr create --base fix/disk-checker-output-reconciliation --title "fix(checkers): use shared write_metrics in run_check" --body "$(cat <<'EOF'
## Summary
- Extracts the disk-aware metrics renderer added in #132 into a shared helper \`write_metrics(stdout, metrics, indent)\`
- \`run_check\` now routes its metrics block through the helper, so \`run_check disk_macos\` (and friends) print readable subtotals/items/trailers instead of dumping list repr
- \`check_health\` is unchanged user-visibly — same output, just delegated through the helper
- 12 format tests migrated from \`CheckHealthCommandTests\` into a new \`test_metrics_format.py\`; 3 wiring tests added to \`RunCheckCommandTests\`

**Stacked on #132.** Base is \`fix/disk-checker-output-reconciliation\`. After #132 merges, retarget to \`main\`.

Design doc: \`docs/plans/2026-05-07-run-check-disk-formatter-design.md\`

## Test plan
- [x] \`uv run pytest apps/checkers/\` — full suite green
- [x] \`uv run coverage report\` — 100% on \`_metrics_format.py\`, \`check_health.py\`, \`run_check.py\`
- [x] \`uv run python manage.py run_check disk_macos\` on a live host — readable output, no list repr, totals reconcile
- [x] \`uv run python manage.py run_check cpu\` — flat keys show \`cpu percent: 15.5\` (new format)
- [x] \`uv run python manage.py run_check disk_macos --json\` — JSON path unaffected

## Visible behavior change
For non-disk checkers, \`run_check\` flat keys now render with underscores stripped:
- Before: \`    cpu_percent: 15.5\`
- After: \`    cpu percent: 15.5\`

This matches \`check_health\`'s long-standing format.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Stacked PR.** Base is `fix/disk-checker-output-reconciliation`, not `main`. After #132 merges, retarget to `main` (GitHub does this automatically; if not, edit the base in the PR UI or via `gh pr edit --base main`).
- **Commit A is a refactor** — output is byte-identical, no user-visible change. If any check_health-related test fails after Task 3, the helper is missing a branch.
- **Commit B is a behavior change** — `run_check` non-disk checkers now show `cpu percent` instead of `cpu_percent`. Document this in the commit message and PR body (already done in templates above).
- **Don't extract a helper for the helper.** The `write_metrics` body is ~50 lines and reads top-to-bottom. Splitting it further adds friction without payoff.
- **Don't add `--verbose`, format toggles, or per-section sort fixes.** Those are out of scope. The global-sort fix lives in issue #133 and gets its own PR.
- **The 12 deleted tests are an archaeological footprint.** PR reviewers may flinch — the commit message in Commit A explicitly explains the migration. Don't try to keep them around as "extra coverage"; they would just duplicate the unit tests and slow the suite.
- **`SimpleTestCase` vs `TestCase` for `WriteMetricsTests`.** The unit tests don't touch the database; `SimpleTestCase` is faster. If your runner complains, fall back to `TestCase`.
