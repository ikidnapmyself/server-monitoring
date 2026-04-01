---
title: "Test Suite Speedup — Design"
parent: Plans
nav_order: 79739694
---
# Test Suite Speedup — Design

## Goal

Reduce full test suite from ~82s to ~55s by fixing the 5 slowest tests (~25s total waste) without changing the behavior they validate.

## Problem

5 tests account for ~25s of the 82s total:

| Test | Time | Root Cause |
|------|------|------------|
| 3 orchestration integration tests | ~5s each | CPU checker: `psutil.cpu_percent(interval=1.0)` × 5 samples |
| 1 disk scanning test | ~5s | Real filesystem scan of `/tmp` via `du` + `Path.rglob` |
| 1 timeout test | ~5s | Intentional `time.sleep(5)` in test thread |

## Fixes

### Fix 1: Mock CPU sampling in 3 integration tests (~15s saved)

Tests: `test_pipeline_with_optional_failing_node`, `test_transform_between_nodes`, `test_context_to_intelligence_pipeline` in `apps/orchestration/_tests/test_integration.py`.

These test **pipeline orchestration logic**, not CPU measurement accuracy. Patch `psutil.cpu_percent` to return a fixed value instantly.

### Fix 2: Reduce sleep in timeout test (~5s saved)

Test: `test_timeout_branch` in `apps/orchestration/_tests/test_nodes.py`.

Change `time.sleep(5)` to `time.sleep(0.5)` — still well above the 0.01s timeout, proves the same behavior 10x faster.

### Fix 3: Mock filesystem in disk scan test (~5s saved)

Test: `test_provider_disk_progress_callback` in `apps/intelligence/_tests/providers/test_local.py`.

Patch `subprocess.run` and/or `Path.rglob` to return fixture data, or use a small temp directory instead of scanning all of `/tmp`.

## Expected Result

| Before | After | Saved |
|--------|-------|-------|
| ~82s | ~55s | ~27s (33%) |

## Out of Scope

- Admin test optimization (inherent DB/rendering costs, diminishing returns)
- pytest-xdist parallelization (separate concern, can be added later)
- Pre-commit hook changes (already fast at ~6s with black + ruff + mypy)
