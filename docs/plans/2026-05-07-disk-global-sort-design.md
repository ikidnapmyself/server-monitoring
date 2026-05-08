---
title: "Disk Checkers: Global Sort Across Scan Targets Design"
parent: Plans
---

# Disk Checkers: Global Sort Across Scan Targets Design

## Problem

The disk analysis checkers (`disk_common`, `disk_macos`, `disk_linux`) collect their `space_hogs`, `old_files`, and `large_files` lists by appending per-target scan results into a single list. Each per-target scanner (`scan_directory`, `find_old_files`, `find_large_files`) returns its own results sorted by `size_mb` descending — but the parent loop simply concatenates those results. The combined list ends up sorted *within each target* but not *across* them.

When the CLI formatter shows the "top 10" of a section, it shows the *first 10 in concatenation order*, not the actual 10 largest items. A 596 MB item from one target can land in position 8 of the displayed top 10, below items in the 1–22 MB range that came from an earlier target. Reconciliation math (added in #132) still works because totals are order-independent, but the "top 10" view doesn't actually show the biggest items — defeating its purpose.

Filed as issue #133.

## Scope

In scope:
- Add a single `.sort(key=lambda x: x["size_mb"], reverse=True)` call after each of the seven per-target collection loops in the three disk checkers.
- Add five focused tests covering the multi-target sort invariant.

Out of scope:
- Extracting a shared helper (`collect_sorted(scanner, targets, ...)`) for the duplicated loop pattern. Tempting — the loop appears 7 times — but it's a separate refactor and a separate PR. Issue #133's suggested fix is literally "add `.sort()`"; this PR keeps that contract. If the helper is wanted later, it can land cleanly on top of this work.
- Changing the per-target scanner contract (`scan_directory`, `find_old_files`, `find_large_files`). They already sort their own output; the bug is at the layer above.
- Anything in `check_health` / `run_check` formatters. They already render whatever order the metrics dict provides.

This branch is independent of PR #132 (`fix/disk-checker-output-reconciliation`) and PR #134 (`fix/run-check-disk-formatter`). Different files. Branched from `main`.

## Approach — Approach A (minimal `.sort()` add, no helper)

After each per-target collection loop, add one line:

```python
results.sort(key=lambda x: x["size_mb"], reverse=True)
```

(where `results` is the appropriate `space_hogs` / `old_files` / `large_files` list).

The user's earlier feedback on PR #132 — *"don't introduce an additional condition just sort however you sort for usual cases"* — applies again here. The minimal additive change is right. Considered alternatives:

- **Helper extraction** in `disk_utils.py` — saves ~7 lines of repetition but adds a new abstraction with its own tests. Out of scope.
- **Sort only multi-target lists** (5 of 7) — saves 2 lines but asks the reader to remember which sections are single-target today. The single-target `.sort()` calls are no-ops on already-sorted input. Uniformity wins.

## Where exactly each sort lands

| File | Section | Sort placement |
|---|---|---|
| `disk_common.py` | `space_hogs` | after the `for target in scan_targets:` loop, before computing `total` |
| `disk_common.py` | `old_files` | after the `for target in old_file_targets:` loop |
| `disk_common.py` | `large_files` | after the `for target in large_file_targets:` loop (single-target today; sort is a no-op but kept for uniformity) |
| `disk_macos.py` | `space_hogs` | after the `for target in scan_targets:` loop |
| `disk_macos.py` | `old_files` | after the `for target in old_file_targets:` loop (single-target today) |
| `disk_linux.py` | `space_hogs` | after the `for target in scan_targets:` loop |
| `disk_linux.py` | `old_files` | after the `for target in old_file_targets:` loop (single-target today) |

The sort happens *before* `total = sum(...)` and `recs = self._build_recommendations(...)`. Both are order-independent (`total` is a sum; `recs` only reads paths), so placement could go either way — but sorting immediately after collection keeps the data invariant clean and the `total = sum(...)` line reads as "sum the (already-sorted) list".

## Testing

### `apps/checkers/_tests/checkers/test_disk_common.py` — 3 new tests

- `test_space_hogs_globally_sorted_across_scan_targets` — mocks `scan_directory` to return small items for `/var/log` and a large item for `~/.cache`. Asserts the resulting `space_hogs` is in descending `size_mb` order with the large item first.
- `test_old_files_globally_sorted_across_targets` — mocks `find_old_files` to return different-sized items for `/tmp` vs `/var/tmp`. Asserts descending order.
- `test_large_files_sorted_descending` — single-target today, but pins the invariant so the test stays valid if `large_file_targets` ever grows.

### `apps/checkers/_tests/checkers/test_disk_macos.py` — 1 new test

- `test_space_hogs_globally_sorted_across_scan_targets` — mocks `scan_directory` returning different-sized items for the four macOS scan targets (`~/Library/Caches`, `/Library/Caches`, `~/Library/Logs`, `~/Library/Developer/Xcode/DerivedData`).

### `apps/checkers/_tests/checkers/test_disk_linux.py` — 1 new test

- `test_space_hogs_globally_sorted_across_scan_targets` — mocks `scan_directory` returning different-sized items for the four Linux scan targets (`/var/cache/apt/archives`, `/var/log/journal`, `/var/lib/docker`, `/var/lib/snapd`).

`old_files` in `disk_macos` and `disk_linux` is single-target — the issue's acceptance criteria covers "the multi-target case", so no test added there. The `.sort()` line is still inserted for uniformity but isn't separately exercised by a new test.

### Coverage

The new `.sort()` lines are unconditional, so every existing test that runs the checker exercises them. Branch coverage stays at 100% on all three checker files.

## Edge cases

- **Empty list.** `[].sort()` is a no-op; subsequent `sum()` already handles empty lists.
- **Equal `size_mb` values.** Python's sort is stable; equal items keep their insertion order. No tie-breaker introduced.
- **Single-target sections.** Per-target scanner already returns sorted results; `.sort()` rebuilds the same order. ~µs of waste per call, harmless.
- **`scan_directory` internal sort.** Each per-target call is already sorted desc; the new sort handles only the across-target case.
- **Performance.** Lists are bounded by per-scanner size floors (1 MB for `scan_directory`, 100 MB for `find_large_files`, no floor for `find_old_files`). Even on a heavily-populated `/tmp`, items are well under the thousands. Sort cost negligible.
- **Future single → multi-target growth.** If a target list ever grows from one to many entries, the sort is already in place. No follow-up needed.

## Notes for implementation

- **No helper extraction.** Inline the sort 7 times. If the duplication ever becomes painful, a follow-up PR can introduce `disk_utils.collect_sorted(scanner, targets, ...)`.
- **Lambda is fine.** `key=lambda x: x["size_mb"]` matches the convention used in `disk_utils.scan_directory` and `find_old_files` already (search for `sorted(results, key=lambda x: ...)`).
- **No changes to the metrics dict shape.** Only the order of items inside the lists changes. Consumers (CLI formatter, JSON output, intelligence layer) already treat the lists as unordered for everything except display ranking.