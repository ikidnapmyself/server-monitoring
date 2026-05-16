---
title: "2026-05-06 Disk Checker Output Reconciliation Design"
parent: Plans
---

# Disk Checker Output Reconciliation Design

## Problem

The `disk_common`, `disk_macos`, and `disk_linux` checkers each report a single `total_recoverable_mb` figure computed across multiple lists (`space_hogs`, `old_files`, `large_files`). The `check_health` CLI formatter prints up to 10 items from each list followed by `... and N more`, then prints the grand total. The displayed items don't reconcile against the total: a 746.7 MB total can show only ~190 MB worth of items, with the rest hidden in the truncated tail and in lists that the user may not even realise are part of the sum.

A real example from a Linux host:

```
[OK] disk_common: Disk analysis: 746.7 MB recoverable
       Space Hogs:
         - /var/log/journal  112.0 MB
         - /var/log/btmp.1  16.8 MB
         ...  (10 items shown, summing to 187.0 MB)
         ... and 8 more
       Total recoverable: 746.7 MB
```

The 10 shown items add to 187 MB; the 8 hidden space_hogs each cap at 4.1 MB (sorted descending), so the rest of the 746 MB total is sitting in `old_files` and/or `large_files` sections that may also be truncated, and at minimum lacks per-section subtotals so the user could decompose the grand total at a glance.

The output is not wrong — every value in `metrics` is correct — it's the formatter that hides the breakdown.

## Scope

In scope:
- Change `_output_metrics()` in `apps/checkers/management/commands/check_health.py` so that:
  - Every non-empty section (`space_hogs`, `old_files`, `large_files`) shows a subtotal in its header.
  - When 2+ sections are non-empty, the section with the largest subtotal is shown in full; the others are still truncated to the first 10 items.
  - Truncated sections show a trailer that includes the byte weight of the omitted items: `... and 8 more  (13.5 MB)`.

Out of scope:
- Changes to checker logic or the `metrics` dict shape. The data is already correct.
- Changes to the JSON output path, `run_check`, `preflight`, or the dashboard formatter.
- Status-threshold tuning (`warning_threshold = 5000.0`, `critical_threshold = 20000.0` in MB stay as-is).
- Aligning `disk_linux` and `disk_macos` with `disk_common` by adding `large_files` scans. Worth doing — both currently emit only `space_hogs` and `old_files` while `disk_common` also walks `~` for large files — but it's a behavioural change with new walk targets and perf implications, not a formatter fix. Deferred to a follow-up.
- DB-size scanning (mysql, mariadb, mongo, meili) as a potential future signal source. Worth considering as a future checker (or extension to disk checkers), out of scope here.

## Approach

### Algorithm

In `_output_metrics()`, replace the current `for key in ("space_hogs", "old_files", "large_files"):` block with logic that:

1. Builds an ordered list of `(key, items, subtotal_mb)` tuples for each non-empty list, in the existing iteration order (`space_hogs`, `old_files`, `large_files`).
2. If there are 2+ entries, determines the "largest" one as the entry with the highest subtotal. `max()` over equal subtotals returns the first encountered, which is the natural order — no explicit tie-breaker needed.
3. For each entry, prints:
   - A header line including the subtotal, item count, and shown count:
     - `Space Hogs: 200.5 MB (18 items, top 10 shown)` when truncated.
     - `Space Hogs: 412.0 MB (24 items, all shown)` when full.
   - The item lines, formatted exactly as today (`- {path}  {size_mb:.1f} MB[ ({age_days}d old)]`).
   - If truncated: a trailer `... and N more  (X.X MB)` where `X.X` is the sum of the omitted items' sizes.

The grand-total line `Total recoverable: 746.7 MB` is unchanged.

### Display rule

- 2+ non-empty sections → largest gets full output (every item, no trailer); others get top 10 + trailer.
- Exactly 1 non-empty section → that section gets top 10 + trailer, just like the multi-section case. We don't promote it to "full output" because the user already has the signal that this single section is the cause; expanding to potentially hundreds of items only bloats the screen.
- 0 non-empty sections → nothing prints (same as today).

### Why this shape

- **Subtotals always shown.** The user can reconcile any subset of sections against the grand total without scrolling.
- **Trailer carries weight.** `... and 8 more  (13.5 MB)` makes truncation honest — you can see the omitted weight without re-running with a flag.
- **Largest-in-full is bounded.** Only one section can be the largest, so the worst case is "one section dumps everything" rather than "every section dumps everything".
- **No new flags.** No `--verbose` or `--full`; the output reads correctly by default. Compact runs over many checkers stay reasonable because most non-disk checkers contribute a single line, and each disk checker contributes at most one fully-expanded section.

## Edge cases

- **All sections empty.** No section header lines print. Total may still print if `total_recoverable_mb` is set. Same as today.
- **Section has ≤ 10 items.** All items already fit; header reads `(N items, all shown)`, no trailer. Applies regardless of largest-section status.
- **`disk_macos` / `disk_linux`** only emit `space_hogs` and `old_files` (no `large_files`). The iteration over the three keys naturally skips empty/missing keys. The largest-of-two rule still works.
- **Float rounding.** Subtotals are computed with regular float sums; rounding drift between subtotals and the grand total is possible at the 0.1 MB level. Acceptable — matches existing rounding behaviour and the underlying `size_mb` values are already rounded at scan time.

## Testing

`apps/checkers/_tests/test_commands.py` already has `test_metrics_space_hogs` covering the truncation + "... and 2 more" string. The new test cases needed:

- Single non-empty section with > 10 items → header shows `(N items, top 10 shown)`, items truncated to 10, trailer carries the omitted weight.
- Two non-empty sections, the second being the largest → first gets truncated header + trailer, second gets `(N items, all shown)` and full item list.
- Three non-empty sections (the `disk_common` shape), `large_files` largest → only `large_files` shown in full.
- Truncated trailer math: 18 items where the bottom 8 sum to a known value → trailer reads that value.
- Section with exactly 10 items → header `(10 items, all shown)`, no trailer.
- Empty metrics dict → no header lines, no trailer.

The existing `test_metrics_space_hogs` will need updating because the "... and 2 more" string is now "... and 2 more  (X.X MB)".

## Notes for implementation

- Keep the change inside `_output_metrics`. Don't extract a helper unless the diff genuinely needs it — the method is already structured around per-section blocks and one extra preprocessing step (compute subtotals, pick the largest) is enough.
- Don't change the order of keys iterated. Today's order is the contract for `_build_recommendations` and for any operator who reads the output regularly.
- The trailer format `... and N more  (X.X MB)` uses two spaces before the parenthesis to match the existing two-space gap between path and size in item lines (`- /var/log/journal  112.0 MB`). This is intentional visual alignment.