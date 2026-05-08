---
title: "Disk Checker Output Reconciliation Implementation Plan"
parent: Plans
---

# Disk Checker Output Reconciliation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the `check_health` CLI output for disk checkers reconcile against the printed grand total by adding per-section subtotals, expanding the largest section in full when 2+ sections are non-empty, and including the omitted byte weight in truncation trailers.

**Architecture:** Single-file change in `apps/checkers/management/commands/check_health.py`, in the `_output_metrics()` method (lines 194–219). No checker logic, model, or metrics-shape changes. Tests live in `apps/checkers/_tests/test_commands.py` next to the existing `test_metrics_space_hogs` and friends.

**Tech Stack:** Python 3, Django management commands, `unittest.TestCase` via `django.test.TestCase`, `pytest` runner, `coverage` for branch coverage.

**Design doc:** `docs/plans/2026-05-06-disk-checker-output-design.md`

**Branch:** `fix/disk-checker-output-reconciliation` (already created with the design doc committed).

---

## Background — what is changing

`apps/checkers/management/commands/check_health.py:194-219` currently prints:

```
       Space Hogs:
         - /var/log/journal  112.0 MB
         ... [up to 10 lines]
         ... and 8 more
       Old Files:
         ...
       Total recoverable: 746.7 MB
```

After the change:

```
       Space Hogs: 200.5 MB (18 items, top 10 shown)
         - /var/log/journal  112.0 MB
         ... [10 lines]
         ... and 8 more  (13.5 MB)
       Old Files: 412.0 MB (24 items, all shown)         ← largest section
         - /tmp/somefile  220.0 MB  (12d old)
         ... [24 lines, full list]
       Large Files: 134.2 MB (3 items, all shown)
         - /home/me/foo.iso  100.1 MB
         ...
       Total recoverable: 746.7 MB
```

Display rule:
- 0 non-empty sections → no headers print.
- Exactly 1 non-empty section → top 10 + trailer with omitted weight (consistent, not promoted to full).
- 2+ non-empty sections → the section with the largest subtotal prints in full (no trailer); others get top 10 + trailer.

Trailer format: `... and N more  (X.X MB)` — two spaces before the parenthesis to match the existing path/size visual gap.

Header format:
- Truncated: `Space Hogs: 200.5 MB (18 items, top 10 shown)`
- Full: `Space Hogs: 412.0 MB (24 items, all shown)`

---

## Task 1: Update the existing test to the new format

The existing test `test_metrics_space_hogs` at `apps/checkers/_tests/test_commands.py:177-188` asserts on the old format (`"... and 2 more"` and `"Space Hogs:"`). Update its assertions to the new format. Since `space_hogs` is the only non-empty section in this test, it falls under the single-section rule → still truncated.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py:177-188`

**Step 1: Read the current test**

Run: `grep -n "test_metrics_space_hogs" apps/checkers/_tests/test_commands.py`
Note current line range so the edit is unambiguous.

**Step 2: Update the assertions**

Replace the body of `test_metrics_space_hogs` (lines 177–188) so it asserts the new header and trailer format. The 12-item input has 10 shown and 2 omitted; each item is 100.5 MB; total 1206.0 MB; omitted weight 201.0 MB.

```python
    def test_metrics_space_hogs(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        mock_checker = self._make_checker(metrics={"space_hogs": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs: 1206.0 MB (12 items, top 10 shown)", output)
        self.assertIn("/tmp/file0", output)
        self.assertIn("100.5 MB", output)
        self.assertIn("30d old", output)
        self.assertIn("... and 2 more  (201.0 MB)", output)
```

**Step 3: Run the test — expect failure**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests::test_metrics_space_hogs -v`
Expected: FAIL — the new strings are not in the output yet.

**Step 4: Do not commit yet.** This test stays failing until Task 6 implements the formatter.

---

## Task 2: Add a test for the "all shown" header case

When a section has ≤ 10 items, the header should read `(N items, all shown)` and no trailer should appear.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py` (add a new method to `CheckHealthCommandTests`)

**Step 1: Add the test**

Place this method directly below `test_metrics_space_hogs`:

```python
    def test_metrics_section_all_shown_when_under_cap(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 10.0} for i in range(5)]
        mock_checker = self._make_checker(metrics={"space_hogs": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs: 50.0 MB (5 items, all shown)", output)
        self.assertNotIn("... and", output)
```

**Step 2: Run the test — expect failure**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests::test_metrics_section_all_shown_when_under_cap -v`
Expected: FAIL — "all shown" string is not in output yet.

---

## Task 3: Add a test for the "largest section gets full output" rule

Two non-empty sections, the second has a larger subtotal. The smaller one stays truncated; the larger one prints all items with `(N items, all shown)`.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py`

**Step 1: Add the test**

```python
    def test_metrics_largest_section_shown_in_full(self):
        # space_hogs: 12 items × 5 MB = 60 MB total (smaller)
        # old_files: 12 items × 50 MB = 600 MB total (larger → full output)
        space_hogs = [{"path": f"/tmp/s{i}", "size_mb": 5.0} for i in range(12)]
        old_files = [{"path": f"/tmp/o{i}", "size_mb": 50.0, "age_days": 7} for i in range(12)]
        mock_checker = self._make_checker(
            metrics={"space_hogs": space_hogs, "old_files": old_files}
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        # Smaller section: truncated, with omitted-weight trailer (2 items × 5 MB = 10 MB)
        self.assertIn("Space Hogs: 60.0 MB (12 items, top 10 shown)", output)
        self.assertIn("... and 2 more  (10.0 MB)", output)
        # Larger section: full output, no trailer for this section
        self.assertIn("Old Files: 600.0 MB (12 items, all shown)", output)
        self.assertIn("/tmp/o11", output)  # the 12th old_files item must be visible
```

**Step 2: Run the test — expect failure**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests::test_metrics_largest_section_shown_in_full -v`
Expected: FAIL.

---

## Task 4: Add a test for the three-section case (`disk_common` shape)

Three non-empty sections with `large_files` largest. Confirm only `large_files` is fully shown.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py`

**Step 1: Add the test**

```python
    def test_metrics_three_sections_largest_wins(self):
        # space_hogs: 11 items × 1 MB = 11 MB
        # old_files: 11 items × 2 MB = 22 MB
        # large_files: 11 items × 100 MB = 1100 MB (largest → full)
        space_hogs = [{"path": f"/v/s{i}", "size_mb": 1.0} for i in range(11)]
        old_files = [{"path": f"/v/o{i}", "size_mb": 2.0, "age_days": 5} for i in range(11)]
        large_files = [{"path": f"/h/l{i}", "size_mb": 100.0} for i in range(11)]
        mock_checker = self._make_checker(
            metrics={
                "space_hogs": space_hogs,
                "old_files": old_files,
                "large_files": large_files,
            }
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs: 11.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Old Files: 22.0 MB (11 items, top 10 shown)", output)
        self.assertIn("Large Files: 1100.0 MB (11 items, all shown)", output)
        # Only large_files item #10 (the 11th) must be visible
        self.assertIn("/h/l10", output)
        self.assertNotIn("/v/s10", output)
        self.assertNotIn("/v/o10", output)
```

**Step 2: Run the test — expect failure**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests::test_metrics_three_sections_largest_wins -v`
Expected: FAIL.

---

## Task 5: Add a test for empty / missing sections

`disk_macos` and `disk_linux` only emit `space_hogs` and `old_files`. Verify the formatter handles missing `large_files` cleanly. Also confirm that a metrics dict with no disk lists at all prints nothing related to disk sections.

**Files:**
- Modify: `apps/checkers/_tests/test_commands.py`

**Step 1: Add the test**

```python
    def test_metrics_no_disk_sections(self):
        mock_checker = self._make_checker(metrics={"cpu_percent": 12.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertNotIn("Space Hogs", output)
        self.assertNotIn("Old Files", output)
        self.assertNotIn("Large Files", output)
        self.assertIn("cpu percent: 12.5", output)
```

**Step 2: Run the test — expect to pass already**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::CheckHealthCommandTests::test_metrics_no_disk_sections -v`
Expected: PASS even before the implementation change, because the current loop just skips empty/missing keys. We add this test so the regression coverage is locked in *before* refactor.

---

## Task 6: Implement the formatter change

Replace the current section loop in `_output_metrics()` with the new logic. Keep the method's other responsibilities (`total_recoverable_mb`, recommendations, flat keys, nested dicts) untouched.

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py:194-219`

**Step 1: Read the current method**

Run: `sed -n '194,250p' apps/checkers/management/commands/check_health.py`
Note exact indentation and context around the section block.

**Step 2: Replace the section block**

Replace lines 198–209 (the current `for key in (...)` loop and trailer print) with this block. Leave the `indent = "       "` line and everything after `total = metrics.get("total_recoverable_mb")` alone.

```python
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
            self.stdout.write(
                f"{indent}{label}: {subtotal:.1f} MB ({len(items)} items, {count_note})"
            )
            for item in shown:
                size = f"{item['size_mb']:.1f} MB"
                extra = f" ({item['age_days']}d old)" if "age_days" in item else ""
                self.stdout.write(f"{indent}  - {item['path']}  {size}{extra}")
            if not show_all and len(items) > cap:
                omitted_weight = sum(it["size_mb"] for it in items[cap:])
                self.stdout.write(
                    f"{indent}  ... and {len(items) - cap} more  ({omitted_weight:.1f} MB)"
                )
```

**Step 3: Run the disk-related tests**

Run: `uv run pytest apps/checkers/_tests/test_commands.py -v -k "metrics"`
Expected: every `test_metrics_*` PASSES, including the four new ones from Tasks 1–5.

**Step 4: Run the full command test module**

Run: `uv run pytest apps/checkers/_tests/test_commands.py -v`
Expected: all PASS. Watch for regressions in unrelated tests like `test_metrics_old_files` / `test_metrics_large_files` (those use 1-item lists, so they fall under "single section, all shown" and assert `Old Files:` / `Large Files:` substrings — the new format still includes those substrings).

---

## Task 7: Run the full checkers test suite

Make sure nothing in the wider checker tests regressed.

**Step 1: Run**

Run: `uv run pytest apps/checkers/ -v`
Expected: all PASS.

**Step 2: If anything fails**

The only realistic failure is a test elsewhere that asserts on the *old* "Space Hogs:" header without subtotal. If found, update the assertion to match the new format — do not change the formatter. Document any unexpected change in your commit message.

---

## Task 8: Coverage check

CLAUDE.md requires 100% branch coverage on every PR.

**Step 1: Run coverage**

Run: `uv run coverage run -m pytest apps/checkers/_tests/test_commands.py && uv run coverage report -m --include='apps/checkers/management/commands/check_health.py'`
Expected: 100% on `check_health.py`. The branches you must cover (and that the new tests already exercise):
- `len(sections) >= 2` true (Tasks 3 and 4) and false (Tasks 1, 2, 5)
- `show_all` true (largest in multi-section, Tasks 3, 4) and false (single-section, Tasks 1, 2; non-largest sections in Tasks 3, 4)
- `len(items) <= cap` true (Tasks 2, 5) and false (Tasks 1, 3, 4)
- trailer printed (Tasks 1, 3, 4) and not printed (Tasks 2, 5)

**Step 2: Fix gaps**

If a branch is uncovered, add a focused test for it before proceeding. Do not commit incomplete coverage.

---

## Task 9: Manual sanity check on a real machine

You're already on Linux (the bug report is from a Linux host). Run the disk checker against the live system.

**Step 1: Run**

Run: `uv run python manage.py run_check disk_common`
Read the output. The total should reconcile against the per-section subtotals (within ~0.1 MB rounding drift), and any truncated section should show an omitted-weight trailer.

Note: `run_check` uses its own simpler metrics formatter (`apps/checkers/management/commands/run_check.py:165-175`) which dumps lists via `str()`. Its output won't show the new headers — that's expected and out of scope. To exercise the new formatter, instead run:

Run: `uv run python manage.py check_health disk_common`
Expected: per-section subtotals visible; if 2+ sections are non-empty, exactly one is fully expanded; truncated sections show `... and N more  (X.X MB)`; the per-section subtotals add to the grand total within rounding.

**Step 2: If totals don't reconcile**

Diff `total_recoverable_mb` against `sum(subtotals)`. Drift > 1 MB indicates a bug — investigate before continuing.

---

## Task 10: Lint, format, type-check

Run the project's standard quality gates.

**Step 1: Run them in parallel**

Run these in one shell:
```
uv run black apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_commands.py && \
uv run ruff check apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_commands.py --fix && \
uv run mypy apps/checkers/management/commands/check_health.py
```
Expected: all clean. Black may reformat; commit those changes alongside the implementation.

---

## Task 11: Commit

**Step 1: Stage and commit**

```bash
git add apps/checkers/management/commands/check_health.py apps/checkers/_tests/test_commands.py
git commit -m "$(cat <<'EOF'
fix(checkers): reconcile disk checker output against grand total

The check_health CLI formatter truncated each disk section to 10 items
without printing per-section subtotals, so the displayed items couldn't
be reconciled against the printed grand total — a 746 MB total could
visibly account for under 200 MB of items.

Per-section subtotals now appear in every section header. When 2+
sections are non-empty the section with the largest subtotal prints in
full (no trailer); other sections keep the 10-item cap with a trailer
that carries the omitted byte weight: "... and 8 more  (13.5 MB)".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Step 2: Push branch and open PR**

```bash
git push -u origin fix/disk-checker-output-reconciliation
gh pr create --title "fix(checkers): reconcile disk checker output against grand total" --body "$(cat <<'EOF'
## Summary
- Adds per-section subtotals to disk-checker output in `check_health`
- When 2+ sections are non-empty, the section with the largest subtotal prints in full so the user can see where the weight is
- Truncated sections now include the omitted byte weight: `... and 8 more  (13.5 MB)`

Design doc: `docs/plans/2026-05-06-disk-checker-output-design.md`

## Test plan
- [x] `uv run pytest apps/checkers/_tests/test_commands.py -v` — all green, four new tests cover single/multi-section, largest-wins, no-disk-sections, all-shown
- [x] `uv run coverage report -m --include='apps/checkers/management/commands/check_health.py'` — 100%
- [x] `uv run python manage.py check_health disk_common` on a live host — subtotals reconcile against the grand total within rounding

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL when done.

---

## Notes for implementer

- **Don't expand scope.** No `--verbose` flag, no `large_files` walk added to `disk_macos`/`disk_linux`, no DB-size scanning. Each is intentionally deferred (see design doc).
- **Don't refactor `_output_metrics` beyond the marked block.** The flat-keys, nested-dicts, and recommendations branches below it are unrelated.
- **Trailer spacing matters.** `... and 8 more  (13.5 MB)` uses two spaces before the parenthesis. The existing item line `- {path}  {size_mb} MB` uses two spaces between path and size. Keep them in visual line.
- **Float math.** Subtotals are computed with regular float `sum()`, not `round()`-then-sum. The header formats with `:.1f`, matching the grand-total formatting at line 213. Tiny rounding drift between subtotals and the grand total is acceptable and matches existing behavior.
- **Tie-breaking on `max()` over equal subtotals.** Python's `max` returns the first encountered, which is the iteration order (`space_hogs` first). Don't add an explicit tie rule.