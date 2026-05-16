---
title: "2026-05-07 Disk Checkers: Global Sort Implementation Plan"
parent: Plans
---

# Disk Checkers: Global Sort Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the disk checkers' `space_hogs` / `old_files` / `large_files` lists be sorted descending by `size_mb` *globally across scan targets*, not just within each target.

**Architecture:** Add one `.sort(key=lambda x: x["size_mb"], reverse=True)` call after each of the seven per-target collection loops in `disk_common.py` / `disk_macos.py` / `disk_linux.py`. No new modules, no helper extraction, no metrics-shape changes. Five new tests cover the multi-target sort invariant.

**Tech Stack:** Python 3, Django management commands, `unittest.TestCase` via `django.test.TestCase`, `pytest` runner, `coverage`.

**Design doc:** `docs/plans/2026-05-07-disk-global-sort-design.md`

**Branch:** `fix/disk-global-sort` (already created from `main` with the design doc committed).

**Single commit planned.** TDD red-green: write 5 failing tests, add 7 sort lines, all green.

**Independent of PR #132 and PR #134** — this branch does not touch `check_health.py`, `run_check.py`, or `_metrics_format.py`. Different files.

---

## Background — what is changing

Each disk checker collects per-target scan results into a single list:

```python
space_hogs = []
seen = set()
for target in scan_targets:
    for item in scan_directory(target, timeout=self.timeout):
        if item["path"] not in seen:
            seen.add(item["path"])
            space_hogs.append(item)
# (issue #133: needs space_hogs.sort(...) here)
```

Each per-target call returns sorted-desc results, but the parent loop just appends — so a 500 MB item from a later target lands after small items from an earlier one. The fix: one `.sort()` after each loop.

**The 7 sort sites:**

| File | Section | Loop ends after current line | Targets |
|---|---|---|---|
| `disk_common.py` | `space_hogs` | `space_hogs.append(item)` | 2 (`/var/log`, `~/.cache`) |
| `disk_common.py` | `old_files` | `old_files.append(item)` | 2 (`/tmp`, `/var/tmp`) |
| `disk_common.py` | `large_files` | `large_files.append(item)` | 1 (single-target today; sort is no-op for uniformity) |
| `disk_macos.py` | `space_hogs` | `space_hogs.append(item)` | 4 |
| `disk_macos.py` | `old_files` | `old_files.append(item)` | 1 (single-target today) |
| `disk_linux.py` | `space_hogs` | `space_hogs.append(item)` | 4 |
| `disk_linux.py` | `old_files` | `old_files.append(item)` | 1 (single-target today) |

---

## Task 1: Add multi-target sort tests to `test_disk_common.py`

Three tests covering all three lists. Place them at the bottom of `DiskCommonCheckerTests`.

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_disk_common.py`

**Step 1: Add the three tests**

Append to `DiskCommonCheckerTests`:

```python
    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_space_hogs_globally_sorted_across_scan_targets(
        self, mock_large, mock_old, mock_scan, mock_expanduser
    ):
        """space_hogs is sorted desc by size_mb across /var/log and ~/.cache."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")

        def fake_scan(path, timeout=None):
            if path == "/var/log":
                return [
                    {"path": "/var/log/a", "size_mb": 5.0},
                    {"path": "/var/log/b", "size_mb": 3.0},
                ]
            if path == "/home/testuser/.cache":
                return [{"path": "/home/testuser/.cache/big", "size_mb": 500.0}]
            return []

        mock_scan.side_effect = fake_scan
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 500.0)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_old_files_globally_sorted_across_targets(
        self, mock_large, mock_old, mock_scan, mock_expanduser
    ):
        """old_files is sorted desc by size_mb across /tmp and /var/tmp."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []

        def fake_old(path, max_age_days=7, timeout=None):
            if path == "/tmp":
                return [{"path": "/tmp/small", "size_mb": 2.0, "age_days": 10}]
            if path == "/var/tmp":
                return [{"path": "/var/tmp/big", "size_mb": 200.0, "age_days": 15}]
            return []

        mock_old.side_effect = fake_old
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["old_files"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 200.0)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_large_files_sorted_descending(
        self, mock_large, mock_old, mock_scan, mock_expanduser
    ):
        """large_files is sorted desc — single-target today, locks invariant for the future."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []
        mock_old.return_value = []
        # Return UNSORTED data to verify the checker sorts it (real find_large_files
        # would already sort, but this test pins the .sort() call regardless).
        mock_large.return_value = [
            {"path": "/home/testuser/small.iso", "size_mb": 150.0},
            {"path": "/home/testuser/big.iso", "size_mb": 800.0},
            {"path": "/home/testuser/medium.iso", "size_mb": 400.0},
        ]

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["large_files"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
```

**Step 2: Run the new tests — expect failure**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_common.py -v -k "globally_sorted or sorted_descending"`

Expected: 3 FAIL.

- `test_space_hogs_globally_sorted_across_scan_targets` fails because the 500 MB item from `~/.cache` lands after the 5 MB and 3 MB items from `/var/log`.
- `test_old_files_globally_sorted_across_targets` fails for the same reason (200 MB after 2 MB).
- `test_large_files_sorted_descending` fails because the mock returns unsorted data and the current code doesn't re-sort.

If any of the three unexpectedly passes, STOP and report — that means the assertion is too weak or the mock isn't being hit.

---

## Task 2: Add multi-target sort test to `test_disk_macos.py`

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_disk_macos.py`

**Step 1: Add the test**

Append to `DiskMacOSCheckerTests`:

```python
    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.scan_directory")
    @patch("apps.checkers.checkers.disk_macos.find_old_files")
    def test_space_hogs_globally_sorted_across_scan_targets(
        self, mock_old, mock_scan, mock_expanduser, mock_sys
    ):
        """space_hogs is sorted desc across the four macOS scan targets."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")

        def fake_scan(path, timeout=None):
            # First (~/Library/Caches) returns a small item;
            # second (/Library/Caches) returns the largest;
            # third (~/Library/Logs) returns a medium item.
            if path == "/Users/testuser/Library/Caches":
                return [{"path": f"{path}/small", "size_mb": 5.0}]
            if path == "/Library/Caches":
                return [{"path": f"{path}/big", "size_mb": 800.0}]
            if path == "/Users/testuser/Library/Logs":
                return [{"path": f"{path}/medium", "size_mb": 100.0}]
            return []

        mock_scan.side_effect = fake_scan
        mock_old.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 800.0)
```

**Step 2: Run — expect failure**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_macos.py -v -k "globally_sorted"`

Expected: FAIL — the 800 MB item lands in position 2 (after the 5 MB item from the first target).

---

## Task 3: Add multi-target sort test to `test_disk_linux.py`

**Files:**
- Modify: `apps/checkers/_tests/checkers/test_disk_linux.py`

**Step 1: Add the test**

Append to `DiskLinuxCheckerTests`:

```python
    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_space_hogs_globally_sorted_across_scan_targets(
        self, mock_old, mock_scan, mock_sys
    ):
        """space_hogs is sorted desc across the four Linux scan targets."""
        mock_sys.platform = "linux"

        def fake_scan(path, timeout=None):
            # apt cache small; journal largest; docker medium.
            if path == "/var/cache/apt/archives":
                return [{"path": f"{path}/small", "size_mb": 50.0}]
            if path == "/var/log/journal":
                return [{"path": f"{path}/big", "size_mb": 900.0}]
            if path == "/var/lib/docker":
                return [{"path": f"{path}/medium", "size_mb": 300.0}]
            return []

        mock_scan.side_effect = fake_scan
        mock_old.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 900.0)
```

**Step 2: Run — expect failure**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_linux.py -v -k "globally_sorted"`

Expected: FAIL — the 900 MB item lands in position 2 (after the 50 MB apt-cache item).

---

## Task 4: Add the seven sort lines

Add `.sort(key=lambda x: x["size_mb"], reverse=True)` after each of the seven per-target collection loops.

**Files:**
- Modify: `apps/checkers/checkers/disk_common.py`
- Modify: `apps/checkers/checkers/disk_macos.py`
- Modify: `apps/checkers/checkers/disk_linux.py`

**Step 1: `disk_common.py`**

Find the `for target in scan_targets:` block (currently lines 36-41). Immediately after the inner loop ends — *before* the comment `# Build set of already-scanned paths to exclude from large file walk` — add:

```python
            space_hogs.sort(key=lambda x: x["size_mb"], reverse=True)
```

Find the `for target in old_file_targets:` block (currently lines 44-49). Immediately after the inner loop ends — *before* the next blank line and the comment about exclude_paths — add:

```python
            old_files.sort(key=lambda x: x["size_mb"], reverse=True)
```

Find the `for target in large_file_targets:` block (currently lines 57-64). Immediately after the inner loop ends — *before* the `total = (...)` line — add:

```python
            large_files.sort(key=lambda x: x["size_mb"], reverse=True)
```

Match the existing 12-space indent of the surrounding code (these blocks are inside `try:` inside `def check():`).

**Step 2: `disk_macos.py`**

Find the `for target in scan_targets:` block (currently lines 39-44). Immediately after the inner loop ends — *before* the `old_files = []` line — add:

```python
            space_hogs.sort(key=lambda x: x["size_mb"], reverse=True)
```

Find the `for target in old_file_targets:` block (currently lines 47-52). Immediately after the inner loop ends — *before* the `total = ...` line — add:

```python
            old_files.sort(key=lambda x: x["size_mb"], reverse=True)
```

**Step 3: `disk_linux.py`**

Find the `for target in scan_targets:` block (currently lines 38-42). Immediately after the inner loop ends — *before* the `old_files = []` line — add:

```python
            space_hogs.sort(key=lambda x: x["size_mb"], reverse=True)
```

Find the `for target in old_file_targets:` block (currently lines 45-49). Immediately after the inner loop ends — *before* the `total = ...` line — add:

```python
            old_files.sort(key=lambda x: x["size_mb"], reverse=True)
```

---

## Task 5: Verify tests pass

**Step 1: Run the five new tests**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py -v -k "globally_sorted or sorted_descending"`

Expected: 5 PASS.

If any fails, the sort line is missing, in the wrong place, or has a typo. Use `git diff` to inspect.

**Step 2: Run the full disk checker test suites**

Run: `uv run pytest apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py -v`

Expected: all PASS. Existing tests should be unaffected — the sort is unconditional and order-preserving for already-sorted input.

If any unrelated test fails: it likely asserts on a specific list order that depended on the old per-target concatenation. Read the failure carefully; if the assertion is genuinely about per-target order rather than global sort, that's a real conflict — STOP and report. If the assertion is just brittle (e.g., asserts position 0 is a specific item that happens to land there because of the old order but isn't actually guaranteed), update it to match the new global-sorted order.

**Step 3: Run the full checkers suite**

Run: `uv run pytest apps/checkers/ -v`

Expected: all PASS.

---

## Task 6: Coverage

CLAUDE.md requires 100% branch coverage on every PR.

**Step 1: Run**

```
uv run coverage run --branch -m pytest apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py
uv run coverage report -m --include='apps/checkers/checkers/disk_common.py,apps/checkers/checkers/disk_macos.py,apps/checkers/checkers/disk_linux.py'
```

Expected: 100% on all three files. The `.sort()` lines are unconditional, so any test that runs `check()` exercises them.

If less than 100%, identify the uncovered lines (likely something pre-existing — investigate, don't add unrelated tests).

---

## Task 7: Lint, format, type-check

```
uv run black --check apps/checkers/checkers/disk_common.py apps/checkers/checkers/disk_macos.py apps/checkers/checkers/disk_linux.py apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py
uv run ruff check apps/checkers/checkers/disk_common.py apps/checkers/checkers/disk_macos.py apps/checkers/checkers/disk_linux.py apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py
uv run mypy apps/checkers/checkers/disk_common.py apps/checkers/checkers/disk_macos.py apps/checkers/checkers/disk_linux.py
```

Expected: all clean.

---

## Task 8: Commit

```bash
git add apps/checkers/checkers/disk_common.py apps/checkers/checkers/disk_macos.py apps/checkers/checkers/disk_linux.py apps/checkers/_tests/checkers/test_disk_common.py apps/checkers/_tests/checkers/test_disk_macos.py apps/checkers/_tests/checkers/test_disk_linux.py
git commit -m "$(cat <<'EOF'
fix(checkers): globally sort disk checker lists across scan targets

Each disk checker collected per-target scan results into a single list
without re-sorting globally. scan_directory / find_old_files /
find_large_files each return sorted-desc results, but appending across
multiple targets left the combined list sorted *within* targets and
not *across* them — so the displayed "top 10" could show small items
above a large one from a later target.

Add one .sort() after each of the seven per-target collection loops
in disk_common, disk_macos, and disk_linux. Five focused tests cover
the multi-target sort invariant.

Closes #133.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, `git status` clean. `git log --oneline main..HEAD` should show 2 commits (design doc + this fix).

---

## Task 9: Push and open PR

```bash
git push -u origin fix/disk-global-sort
gh pr create --title "fix(checkers): globally sort disk checker lists across scan targets" --body "$(cat <<'EOF'
## Summary
- Adds one \`.sort(key=lambda x: x["size_mb"], reverse=True)\` after each of the seven per-target collection loops in \`disk_common.py\`, \`disk_macos.py\`, and \`disk_linux.py\`
- Disk checker output now shows the actually-largest items first across scan targets, not just within each target
- 5 focused tests pin the multi-target sort invariant; 7 unconditional sort lines added; no metrics-shape change

Design doc: \`docs/plans/2026-05-07-disk-global-sort-design.md\`

Closes #133.

## Why
Before this PR, a 596 MB item from \`~/.cache\` could land in position 8 of \`disk_common\`'s "top 10" \`space_hogs\` list, below items in the 1–22 MB range that came from an earlier scan target (\`/var/log\`). Reconciliation math (added in #132) still works because totals are order-independent — but the truncated "top 10" view doesn't actually show the biggest items, defeating its purpose.

## Test plan
- [x] \`uv run pytest apps/checkers/\` — full suite green; 5 new tests cover space_hogs/old_files/large_files sort across multi-target loops in all three checkers
- [x] \`uv run coverage report\` — 100% on disk_common.py, disk_macos.py, disk_linux.py
- [x] black / ruff / mypy clean

## Out of scope
- Extracting a shared helper for the duplicated loop pattern (the sort/dedup/append shape appears 7 times). Tempting, but a separate refactor — issue #133's suggested fix is literally "add \`.sort()\`", and this PR keeps that contract.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Just inline the sort.** Do not extract a helper, even if the duplication is annoying.
- **Match existing indentation.** The collection loops sit inside `try:` inside `def check():` — that's 12 spaces of indent on the `space_hogs.sort(...)` line.
- **`large_files` in `disk_common` is single-target today.** The sort is added for uniformity (per Approach A in the design) and is a no-op on already-sorted input. The test `test_large_files_sorted_descending` passes UNSORTED data to the mock, which forces the test to depend on the new sort line — without it, the test fails. Don't worry that it "shouldn't" be needed; it locks the invariant for the future.
- **Don't change `disk_utils.py`.** The per-target scanners already sort correctly; the bug is only at the aggregation layer.
- **Don't introduce a tie-breaker.** Python's sort is stable; equal `size_mb` values keep insertion order. The user previously rejected explicit tie-breaking ("just sort however you sort for usual cases" — PR #132 review).
- **Branch is independent of #132 and #134.** This PR can merge in any order relative to those.