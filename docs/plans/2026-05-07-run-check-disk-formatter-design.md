---
title: "2026-05-07 run_check Disk Formatter Design"
parent: Plans
---

# `run_check` Disk Formatter Design

## Problem

`apps/checkers/management/commands/run_check.py` formats `result.metrics` with a naive loop that prints any non-dict value via `f"{key}: {value}"`. For a disk checker, `space_hogs` / `old_files` / `large_files` are lists of dicts, so the output dumps the Python `repr()` — a single-line wall of text — instead of a readable breakdown:

```
[WARNING] disk_macos
  Disk analysis: 16468.8 MB recoverable

  Metrics:
    platform: darwin
    space_hogs: [{'path': '/Users/...
```

`check_health` already renders disk metrics readably (PR #132 rewired `_output_metrics` to print per-section subtotals, full-largest-section, and a byte-accurate trailer). `run_check` was deliberately scoped out of that PR. The user hits this on every `run_check disk_macos` invocation, so it's now in scope.

## Scope

In scope:
- Extract the metrics-rendering logic from `check_health.Command._output_metrics` into a shared helper `write_metrics(stdout, metrics, indent)` in a new module `apps/checkers/management/commands/_metrics_format.py`.
- Make `check_health._output_metrics` a thin wrapper over the helper.
- Replace `run_check._output_text`'s naive metrics loop with a call to the helper, keeping `run_check`'s existing `"  Metrics:"` wrapper line and 4-space body indent.
- Migrate the existing disk-format tests from `CheckHealthCommandTests` into a new `_tests/test_metrics_format.py` that calls `write_metrics` directly. Replace the migrated tests in `CheckHealthCommandTests` with one wiring smoke test.
- Add three wiring tests in `RunCheckCommandTests` covering the `Metrics:` wrapper, disk section format under run_check, and the new flat-key `cpu percent: 15.5` rendering.

Out of scope:
- Issue #133 (global-sort across scan targets in the checkers themselves) — separate concern, separate PR.
- The `run_check --json` path. Untouched; `json.dumps()` already produces correct output.
- Skipped-checker handling. The existing `skipped` guard in `run_check` stays in place.
- Any other commands (`preflight`, `check_health`'s remaining behavior) beyond the wrapper change.
- Rewriting the helper's algorithm. Behavior is identical to today's `check_health._output_metrics`; only the call surface changes.

This PR is **stacked on PR #132** (`fix/disk-checker-output-reconciliation`). The helper extracts the post-#132 implementation of `_output_metrics`. Branching from `main` (pre-#132) would re-extract the broken formatter.

## Approach

### Helper function

`apps/checkers/management/commands/_metrics_format.py`:

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
    # Body of the current check_health._output_metrics, with
    # self.stdout -> stdout and the local indent value lifted to a
    # parameter. No algorithmic changes.
```

The signature takes `stdout` (any object with a `write(str)` method — `Command.stdout` and Django's `OutputWrapper` both qualify) and `indent` as a string. The function returns `None` and writes side-effectfully, mirroring the existing call style.

### `check_health` integration

`check_health.Command._output_metrics` becomes:

```python
def _output_metrics(self, metrics: dict):
    """Print key metrics below the checker result line."""
    write_metrics(self.stdout, metrics, indent="       ")
```

The 7-space indent (currently a local) is now passed explicitly. No behavior change — the helper's output is byte-identical to what the inlined version produces.

### `run_check` integration

In `run_check.Command._output_text`, replace lines 165-175 (the existing `for key, value in result.metrics.items()` block) with a call to the helper:

```python
if result.metrics and not skipped:
    self.stdout.write("")
    self.stdout.write("  Metrics:")
    write_metrics(self.stdout, result.metrics, indent="    ")
```

The `"  Metrics:"` wrapper and 4-space body indent are run_check's command-specific chrome, kept as-is. Inside the body, every key now goes through the same logic as `check_health`:

- Disk lists (`space_hogs`, `old_files`, `large_files`) get per-section headers, item bullets, and trailers with omitted weight.
- `total_recoverable_mb` and `recommendations` get their dedicated rendering.
- Other scalar values get `key with underscores stripped: value` formatting (with `:.1f` for floats).
- Other dicts get nested rendering.
- The `platform` key (and the others in the `skip` set) is silently elided, matching `check_health`.

This is a small visible change for non-disk run_check output:

- Old: `    cpu_percent: 15.5`
- New: `    cpu percent: 15.5`

User confirmed this consistency with `check_health` is the desired direction.

## Edge cases

- **`run_check --json`** — untouched. JSON path bypasses `_output_text` entirely.
- **Skipped checkers** — `run_check`'s existing `skipped` guard suppresses the entire metrics block, including the `"  Metrics:"` line. Same as today.
- **Empty `metrics={}`** — `if result.metrics and not skipped:` keeps the `"  Metrics:"` line from printing when the dict is empty. `check_health` calls the helper with `{}` and produces nothing. Both unchanged.
- **Non-disk metric that is a list** (hypothetical, e.g. `errors: [...]`). Today `run_check` would print `repr()`; with the helper the list isn't in the disk-section keys, isn't in the flat-keys filter (excluded by `not isinstance(v, (list, dict))`), and isn't in `nested` (which only takes dicts). It silently disappears. Acceptable: matches current `check_health` behavior, and no checker emits non-disk lists today. If one is added later, the helper can grow a list-rendering branch.
- **`result.metrics` for `check_health` non-disk checkers** (cpu, memory, network, process). Disk-section branch is a no-op; flat-key + nested-dict branches handle them. Current behavior preserved.
- **Skip set contains `platform`** — disk checkers add `platform` to metrics for diagnostic purposes; the helper elides it from output. Matches today.

## Testing

### New unit tests — `apps/checkers/_tests/test_metrics_format.py`

Twelve tests calling `write_metrics(out, metrics, indent=...)` directly. Most are migrated from `CheckHealthCommandTests` with the same assertion strings; the call mechanism changes from `call_command(...)` to a direct invocation.

1. `test_no_disk_sections` — `metrics={"cpu_percent": 12.5}` → `"cpu percent: 12.5"`, no section headers.
2. `test_section_all_shown_when_under_cap` — 5 items × 10 MB → `"Space Hogs: 50.0 MB (5 items, all shown)"`, no trailer.
3. `test_section_truncated_with_trailer` — 12 items × 100.5 MB → `"Space Hogs: 1206.0 MB (12 items, top 10 shown)"` + `"... and 2 more  (201.0 MB)"`.
4. `test_largest_section_shown_in_full` — two sections (12 × 5 MB and 12 × 50 MB) → larger one full, smaller truncated with trailer.
5. `test_three_sections_largest_wins` — three sections (11 × 1 MB, 11 × 2 MB, 11 × 100 MB) → only `large_files` full.
6. `test_old_files_section_with_age_annotation` — single old_files entry → `"30d old"` annotation visible.
7. `test_large_files_section` — single `large_files` entry → header + bullet, no age annotation.
8. `test_total_recoverable` — `metrics={"total_recoverable_mb": 500.0}` → `"Total recoverable: 500.0 MB"`.
9. `test_recommendations` — `metrics={"recommendations": ["clean /tmp"]}` → `"Recommendations:"` + `"- clean /tmp"`.
10. `test_nested_dict` — nested dict + scalar children render correctly.
11. `test_flat_key_underscore_to_space_and_float_format` — `cpu_percent` → `cpu percent` with `:.1f` formatting.
12. `test_indent_parameter` — pass `indent="    "` and assert the output uses 4-space indent. Locks the parameterization contract.

### Updated tests — `CheckHealthCommandTests`

Remove the five disk-format tests (`test_metrics_space_hogs`, `test_metrics_section_all_shown_when_under_cap`, `test_metrics_largest_section_shown_in_full`, `test_metrics_three_sections_largest_wins`, `test_metrics_no_disk_sections`) and the four flat/nested tests that overlap (`test_metrics_old_files`, `test_metrics_large_files`, `test_metrics_total_recoverable_mb`, `test_metrics_recommendations`, `test_metrics_nested_dict`, plus the float/int format tests if redundant). Replace with one wiring smoke test:

- `test_check_health_uses_metrics_formatter` — `metrics={"space_hogs": [...]}`, asserts the section header substring is in the output. Verifies the command wires up `write_metrics`; format details are covered by the unit tests.

The other `CheckHealthCommandTests` (status styling, summary, exit codes, etc.) are unchanged.

### New tests — `RunCheckCommandTests`

Three wiring tests:

- `test_run_check_wraps_metrics_with_label` — asserts `"  Metrics:"` appears in run_check output.
- `test_run_check_disk_metrics_uses_section_format` — mock disk checker with `space_hogs` (12 items), assert section header appears under `"  Metrics:"` with 4-space indent.
- `test_run_check_flat_metric_uses_helper_format` — assert `"cpu percent: 15.5"` (new format) instead of `"cpu_percent: 15.5"` (old).

### Coverage

CLAUDE.md requires 100 % branch coverage. After this PR:
- `_metrics_format.py` is covered by the 12 unit tests.
- `check_health.py` and `run_check.py` shed coverage as their inlined logic moves to the helper; the new wiring tests exercise the remaining branches.

## Notes for implementation

- **Branch is stacked on `fix/disk-checker-output-reconciliation`** (HEAD `6c7d6d4`). Do not branch from `main`.
- **Algorithm is unchanged.** The helper is a copy-paste-and-parameterize, not a rewrite. Any temptation to clean up float drift, magic numbers (`cap = 10`), or naming is out of scope — those are PR #132's call.
- **No new flags.** No `--verbose`, no format toggles. The output reads correctly by default.
- **Module name has a leading underscore** (`_metrics_format.py`) to signal "internal helper, not a public API."
- **Test churn is real.** Migrating ~9 tests across files is the bulk of the diff. Keep the migration mechanical: same assertions, swap the call mechanism, drop the `mock_checker`/`patch.dict`/`call_command` machinery.