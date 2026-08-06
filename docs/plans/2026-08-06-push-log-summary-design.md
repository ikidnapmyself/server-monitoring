---
title: "Push Log Summary (de-bloat push.log)"
parent: Plans
---

# Push Log Summary — de-bloat `push.log`

**Date:** 2026-08-06
**Status:** Approved (design)
**App:** `apps.alerts` (`push_to_hub` command) + `bin/install/cron.sh`

## Problem

Operators check the last push with `cat logs/push.log`, but the file is an
unreadable pile of full JSON payloads. Root cause: the install cron runs
`push_to_hub --json >> logs/push.log 2>&1` (`bin/install/cron.sh`), and
`--json` prints the entire indented payload — every checker alert with its
full `metrics` dict — on every run. The useful "what happened on the last
push" signal is buried and duplicated.

## Scope

**In:** make a push record a concise, useful summary instead of the payload.

**Explicitly deferred** (a separate, "all together" logging pass — the team
may adopt an external logging tool): log rotation, size caps, and unifying
the other log surfaces (`django.log`, `events.jsonl`, `heartbeats.jsonl`,
`checks.log`, `update.log`). This change adds **no** rotation and **no**
`settings.LOGGING` handler, so it does not pre-commit to a logging design
that the later pass will rework.

## Approach

The bloat comes from `--json` in cron. Fix it at the source:

1. **`push_to_hub` emits a concise, timestamped summary** on the real push
   path (default, non-`--json` output). A pure function
   `summarize_push(...) -> str` builds the text so it is unit-testable
   independently of I/O.

   Format:
   ```
   2026-08-06T03:00:01Z push OK  hub=https://hub.example.com
     ok=6 warning=1 critical=0 -> 7 alerts, HTTP 202 (312ms)
     firing: raid(critical), disk_linux(warning)
   ```
   - Command-emitted UTC ISO-8601 timestamp (cron does not add one), so
     `cat logs/push.log` is self-describing.
   - Counts by checker result status; total alert count; HTTP status; hub
     round-trip duration in ms.
   - `firing:` lists only the non-OK checkers as `checker(severity)` — titles,
     not payloads. Line omitted when nothing is firing.
   - On failure → a one-line `push FAILED hub=… HTTP 500` (or
     `push FAILED hub=… unreachable: <reason>`) to stderr, then the command
     still exits non-zero so cron failures remain detectable.

2. **`--json` is retained** for manual/debug use (prints the full payload); it
   is simply no longer what cron runs.

3. **`bin/install/cron.sh`:** drop `--json` from the push command; keep the
   `>> logs/push.log 2>&1` redirect. `push.log` now accumulates small
   summaries. (Both success → stdout and failure → stderr are captured.)

## Redaction (AGENTS.md discipline)

- **Never** emit the payload or `metrics`.
- **Never** emit `HUB_API_KEY` or the `Authorization` header. Only the
  non-secret `hub_url` appears.

## Public interface

`apps/alerts/management/commands/push_to_hub.py`:

```python
def summarize_push(
    *,
    hub_url: str,
    alerts: list[dict],
    http_status: int | None,
    duration_ms: int | None,
    ok: bool,
    error: str | None = None,
) -> str:
    """Build the one-block push summary line(s) for push.log. Pure; no I/O."""
```

`Command.handle` times the `send_to_hub` call, then prints
`summarize_push(...)` (stdout on success, stderr on failure) on the real push
path instead of the payload.

## Counts / severity mapping

Reuse the existing `_result_to_alert` mapping. Summary counts are derived from
the alerts' `severity`/`status`:

- `ok` = alerts with `status == "resolved"` (CheckStatus.OK)
- `warning` = `severity == "warning"` and firing
- `critical` = `severity == "critical"` and firing
- `firing` list = every non-resolved alert as `checker(severity)`, ordered
  critical-first then warning.

## Error handling & edge cases

- Non-2xx HTTP → log `push FAILED … HTTP <status>`, raise `CommandError`.
- Unreachable / SSRF-blocked hub → log `push FAILED … unreachable: <reason>`,
  raise `CommandError`.
- Zero alerts → `-> 0 alerts`, no `firing:` line.
- Individual checker failure (already handled) → still counted out of the
  summary; the existing per-checker WARNING to stderr is kept.
- `--dry-run` unchanged (still shows the payload for inspection).

## Tests

`apps/alerts/_tests/management/commands/test_push_to_hub.py`:

- `summarize_push` formatting: mixed ok/warning/critical with a `firing:`
  line ordered critical-first; the all-OK case (no `firing:` line); HTTP +
  duration rendering.
- Redaction: payload/`metrics` values and a sentinel api-key never appear in
  the returned string.
- Success path prints the summary (not the payload) to stdout; failure path
  prints `push FAILED` to stderr and exits non-zero.
- `--json` still prints the full payload (unchanged behavior).

## Acceptance criteria

- Cron push writes a concise, timestamped summary to `push.log`; no payload,
  no secrets.
- `--json` and `--dry-run` retain their payload output for manual use.
- 100% branch coverage on changed lines; `black`/`ruff`/`bandit`/`pytest`
  clean.
- No rotation and no `settings.LOGGING` change (deferred to the logging pass).

## Out of scope / follow-ups

- Rotation and size caps for `push.log` and the other log files.
- Unifying log formats / adopting an external logging tool.
- Truncating the existing bloated `push.log` on deploy (operational note, not
  code).
