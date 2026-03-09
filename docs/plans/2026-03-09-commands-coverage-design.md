---
title: "Management Commands Coverage Design"
parent: Plans
---

# Management Commands Coverage Design

## Context

`apps/orchestration/management/commands/` has two files below 100% branch coverage:

- `monitor_pipeline.py` — 61% (list_runs, show_run_details error/stage paths untested)
- `run_pipeline.py` — 95% (various display branches, exception wrapping, edge cases)

`setup_instance.py` and `show_pipeline.py` are already at 100%.

## Approach

Coverage-first: write tests against current code to lock in behavior, then clean up.

## monitor_pipeline.py (61% → 100%)

No cleanup needed — file is small (108 lines), clean, modern syntax.

### Missing tests

| Test | Lines covered |
|------|-------------|
| `list_runs` with results | 52-66: queryset iteration, table output |
| `list_runs` with no results | 57-59: empty queryset warning |
| `list_runs` filtered by status | 54: `status__iexact` filter |
| `show_run_details` not found | 73-75: `DoesNotExist` error |
| `show_run_details` with error + stages | 97, 102-106: `last_error_message`, stage `error_message` |

## run_pipeline.py (95% → 100%)

No cleanup needed — already uses modern type syntax, no dead code.

### Missing tests

| Test | Lines/branches covered |
|------|----------------------|
| Generic exception → CommandError | 179-180 |
| Invalid JSON in `--file` | 196-197 |
| `stage_result` with `.to_dict()` (not dict) | 341 |
| Dry run node with empty config | 359→361 |
| NOTIFY stage in hardcoded `_display_result` | 424→432 |
| Stage errors non-empty | 434 |
| `final_error` with `stack_trace` | 455-458 |
| Intelligence summary > 100 chars | 522 |
| Intelligence provider is None | 525→551 |
| Failed definition result with `error` string | 569 |

## Target

100% branch coverage on all files in `apps/orchestration/management/commands/`.