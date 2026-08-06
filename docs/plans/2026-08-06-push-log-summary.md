---
title: "Push Log Summary Implementation Plan"
parent: Plans
---

# Push Log Summary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make each hub push write a concise, timestamped summary to `push.log` instead of the full JSON payload, and stop the install cron from dumping the payload.

**Architecture:** Add a pure, testable `summarize_push(...) -> str` to the existing `push_to_hub` management command; call it on the real push path (stdout on success, stderr on failure) in place of the payload dump. Drop `--json` from the cron command in `bin/install/cron.sh`. No rotation, no `settings.LOGGING` change (deferred to a later logging pass).

**Tech Stack:** Django management command, pytest/pytest-django, stdlib only.

**Design doc:** `docs/plans/2026-08-06-push-log-summary-design.md`

**Reference before starting:**
- `apps/alerts/management/commands/push_to_hub.py` — the command. Key spots: `handle()` (the `--json`/success/failure branches around lines 123–153), `_result_to_alert()` (severity/status mapping), `send_to_hub()`.
- `apps/alerts/_tests/commands/test_push_to_hub.py` — existing test style: `override_settings(HUB_URL=..., HUB_API_KEY=...)`, `@patch(...CHECKER_REGISTRY)`, `@patch(...safe_urlopen)`, `call_command(..., stdout=StringIO())`.
- `bin/install/cron.sh` — the push cron line (128) and log-path echoes (138, 159).

**Conventions:** absolute imports; line length 100; black + ruff clean; 100% branch coverage on changed lines; never log secrets/payloads (AGENTS.md).

---

## Alert dict shape (from `_result_to_alert`)

Each alert has: `name` (`"<checker>: <message>"`), `status` (`"resolved"`|`"firing"`), `severity` (`"info"`|`"warning"`|`"critical"`), and `labels["checker"]`. The summary derives counts and the firing list from these fields.

- `ok` count = alerts with `status == "resolved"`.
- `warning` count = alerts with `status == "firing"` and `severity == "warning"`.
- `critical` count = alerts with `status == "firing"` and `severity == "critical"`.
- `firing:` list = every `status == "firing"` alert rendered `labels["checker"](severity)`, critical-first then warning.

---

## Task 1: `summarize_push` — success summary

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py`
- Test: `apps/alerts/_tests/commands/test_push_to_hub.py`

**Step 1: Write the failing test**

Add a new test class:

```python
from apps.alerts.management.commands.push_to_hub import summarize_push


class SummarizePushTests(TestCase):
    def _alerts(self):
        return [
            {"name": "cpu: OK", "status": "resolved", "severity": "info",
             "labels": {"checker": "cpu"}},
            {"name": "disk_linux: high", "status": "firing", "severity": "warning",
             "labels": {"checker": "disk_linux"}},
            {"name": "raid: degraded", "status": "firing", "severity": "critical",
             "labels": {"checker": "raid"}},
        ]

    def test_success_summary_has_counts_http_and_firing(self):
        text = summarize_push(
            hub_url="https://hub.example.com",
            alerts=self._alerts(),
            http_status=202,
            duration_ms=312,
            ok=True,
        )
        self.assertIn("push OK", text)
        self.assertIn("hub=https://hub.example.com", text)
        self.assertIn("ok=1 warning=1 critical=1 -> 3 alerts", text)
        self.assertIn("HTTP 202", text)
        self.assertIn("(312ms)", text)
        # firing line, critical first
        self.assertIn("firing: raid(critical), disk_linux(warning)", text)

    def test_all_ok_has_no_firing_line(self):
        text = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[{"name": "cpu: OK", "status": "resolved", "severity": "info",
                     "labels": {"checker": "cpu"}}],
            http_status=202, duration_ms=5, ok=True,
        )
        self.assertIn("ok=1 warning=0 critical=0 -> 1 alerts", text)
        self.assertNotIn("firing:", text)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py::SummarizePushTests -v`
Expected: FAIL — cannot import `summarize_push`.

**Step 3: Write minimal implementation**

Add near the top of `push_to_hub.py` (after `send_to_hub`), importing `datetime`/`timezone` (already imported):

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
    """Build the concise push.log summary block. Pure — no I/O, no secrets.

    Never includes the payload/metrics or the API key; only the non-secret
    hub_url, counts, HTTP status, and the firing checker names.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not ok:
        detail = f"HTTP {http_status}" if http_status is not None else f"unreachable: {error}"
        return f"{ts} push FAILED hub={hub_url} {detail}"

    firing = [a for a in alerts if a.get("status") == "firing"]
    n_ok = sum(1 for a in alerts if a.get("status") == "resolved")
    n_warn = sum(1 for a in firing if a.get("severity") == "warning")
    n_crit = sum(1 for a in firing if a.get("severity") == "critical")

    dur = f" ({duration_ms}ms)" if duration_ms is not None else ""
    lines = [
        f"{ts} push OK  hub={hub_url}",
        f"  ok={n_ok} warning={n_warn} critical={n_crit} -> {len(alerts)} alerts, "
        f"HTTP {http_status}{dur}",
    ]

    if firing:
        order = {"critical": 0, "warning": 1}
        firing_sorted = sorted(firing, key=lambda a: order.get(a.get("severity"), 2))
        parts = [f"{a['labels']['checker']}({a['severity']})" for a in firing_sorted]
        lines.append("  firing: " + ", ".join(parts))

    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py::SummarizePushTests -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "feat(alerts): add summarize_push for concise push.log lines"
```

---

## Task 2: `summarize_push` — failure summary + redaction tests

**Files:**
- Modify: `apps/alerts/_tests/commands/test_push_to_hub.py`

**Step 1: Write the failing/needed tests**

```python
    def test_failed_http_summary(self):
        text = summarize_push(
            hub_url="https://hub.example.com", alerts=[],
            http_status=500, duration_ms=None, ok=False,
        )
        self.assertIn("push FAILED", text)
        self.assertIn("hub=https://hub.example.com", text)
        self.assertIn("HTTP 500", text)

    def test_failed_unreachable_summary(self):
        text = summarize_push(
            hub_url="https://hub.example.com", alerts=[],
            http_status=None, duration_ms=None, ok=False, error="timed out",
        )
        self.assertIn("unreachable: timed out", text)

    def test_summary_never_leaks_payload_or_key(self):
        alerts = [{"name": "cpu: OK", "status": "resolved", "severity": "info",
                   "labels": {"checker": "cpu"},
                   "metrics": {"secret_metric": "sensitive-value-XYZ"}}]
        text = summarize_push(
            hub_url="https://hub.example.com", alerts=alerts,
            http_status=202, duration_ms=1, ok=True,
        )
        self.assertNotIn("sensitive-value-XYZ", text)
        self.assertNotIn("metrics", text)
```

**Step 2: Run to verify**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py::SummarizePushTests -v`
Expected: PASS (Task 1 implementation already satisfies these). If any FAIL, adjust `summarize_push` minimally.

**Step 3: Adjust implementation if needed** — none expected; `summarize_push` reads only `status`/`severity`/`labels.checker`, never `metrics`.

**Step 4: Run to verify it passes** — Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "test(alerts): failure + redaction cases for summarize_push"
```

---

## Task 3: Wire the summary into `handle()` (success path)

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py`
- Test: `apps/alerts/_tests/commands/test_push_to_hub.py`

**Step 1: Write the failing test**

```python
    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_default_output_is_summary_not_payload(self, mock_urlopen, mock_registry):
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.WARNING, message="CPU at 75%",
            metrics={"cpu_percent": 75.0}, checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        resp = MagicMock()
        resp.status = 202
        resp.read.return_value = b"ok"
        mock_urlopen.return_value.__enter__.return_value = resp

        out = StringIO()
        call_command("push_to_hub", stdout=out)
        output = out.getvalue()

        self.assertIn("push OK", output)
        self.assertIn("HTTP 202", output)
        self.assertIn("firing: cpu(warning)", output)
        # payload/metrics must NOT be dumped
        self.assertNotIn("cpu_percent", output)
        self.assertNotIn("\"metrics\"", output)
```

This test is placed in the existing `PushToHubTests` class (reuses its imports/patterns).

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py::PushToHubTests::test_default_output_is_summary_not_payload -v`
Expected: FAIL — current success path prints `"Hub accepted: HTTP 202"` (no firing list) and the `--json` path would dump metrics; the new assertions on `firing:` fail.

**Step 3: Implement — time the call and print the summary**

In `handle()`, replace the success/`--json` block. Add `import time` at top. Around the `send_to_hub` call:

```python
        start = time.perf_counter()
        try:
            status, resp_body = send_to_hub(hub_url, api_key, payload)
        except URLNotAllowedError:
            self.stderr.write(summarize_push(
                hub_url=hub_url, alerts=alerts, http_status=None,
                duration_ms=None, ok=False, error="URL not allowed by security policy",
            ))
            raise CommandError("HUB_URL not allowed by security policy")
        except Exception as e:
            self.stderr.write(summarize_push(
                hub_url=hub_url, alerts=alerts, http_status=None,
                duration_ms=None, ok=False, error=str(e),
            ))
            raise CommandError(f"Failed to reach hub at {hub_url}: {e}")
        duration_ms = int((time.perf_counter() - start) * 1000)

        if status in (200, 201, 202):
            if options["json_output"]:
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            else:
                self.stdout.write(summarize_push(
                    hub_url=hub_url, alerts=alerts, http_status=status,
                    duration_ms=duration_ms, ok=True,
                ))
        else:
            self.stderr.write(summarize_push(
                hub_url=hub_url, alerts=alerts, http_status=status,
                duration_ms=duration_ms, ok=False,
            ))
            raise CommandError(f"Hub returned HTTP {status}: {resp_body}")
```

Also remove the now-redundant early `self.stdout.write(f"Pushing {len(alerts)} alert(s)...")` line (lines 131–132) so the default output is exactly the summary. Keep `--json` (still dumps payload) and `--dry-run` untouched.

**Step 4: Run to verify it passes**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: PASS. Update any existing assertion that expected the old `"Hub accepted: HTTP ..."` wording or the `"Pushing N alert(s)"` line.

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "feat(alerts): push_to_hub prints concise summary, not payload"
```

---

## Task 4: Failure path + `--json` regression tests

**Files:**
- Modify: `apps/alerts/_tests/commands/test_push_to_hub.py`

**Step 1: Write the tests**

```python
    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_failed_http_writes_summary_to_stderr_and_raises(self, mock_urlopen, mock_registry):
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        resp = MagicMock()
        resp.status = 500
        resp.read.return_value = b"boom"
        mock_urlopen.return_value.__enter__.return_value = resp

        err = StringIO()
        with self.assertRaises(CommandError):
            call_command("push_to_hub", stderr=err)
        self.assertIn("push FAILED", err.getvalue())
        self.assertIn("HTTP 500", err.getvalue())

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_json_flag_still_dumps_payload(self, mock_urlopen, mock_registry):
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={"cpu_percent": 10.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        resp = MagicMock()
        resp.status = 202
        resp.read.return_value = b"ok"
        mock_urlopen.return_value.__enter__.return_value = resp

        out = StringIO()
        call_command("push_to_hub", "--json", stdout=out)
        self.assertIn("cpu_percent", out.getvalue())  # payload preserved for debug
```

**Step 2: Run to verify**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: PASS with Task 3 implementation.

**Step 3: Adjust if needed** — ensure the unreachable branch is also covered; if coverage misses the `except Exception` path, add a test where `safe_urlopen` raises `URLError`/generic exception and assert `unreachable:` in stderr.

**Step 4: Run full command test file** — Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "test(alerts): failure-to-stderr + --json payload regression"
```

---

## Task 5: Drop `--json` from the install cron

**Files:**
- Modify: `bin/install/cron.sh`

**Step 1: Edit the push command (line 128)**

Change:
```sh
PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub --json >> ${LOG_DIR:-$PROJECT_DIR/logs}/push.log 2>&1"
```
to (drop `--json`; keep the redirect):
```sh
PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub >> ${LOG_DIR:-$PROJECT_DIR/logs}/push.log 2>&1"
```

**Step 2: Verify shell syntax**

Run: `bash -n bin/install/cron.sh`
Expected: no output (syntax OK).

**Step 3: Sanity-check no other `--json` push references remain**

Run: `grep -n "push_to_hub --json" bin/`
Expected: no matches.

**Step 4: Commit**

```bash
git add bin/install/cron.sh
git commit -m "chore(install): cron push writes summary to push.log, not payload"
```

---

## Task 6: Coverage, lint, docs, verification

**Step 1: Branch coverage on changed code**

Run:
```bash
uv run coverage run -m pytest apps/alerts/_tests/commands/test_push_to_hub.py
uv run coverage report -m --include="*/apps/alerts/management/commands/push_to_hub.py"
```
Expected: 100% on the changed lines. Add targeted tests for any uncovered branch (e.g. the `duration_ms is None` path in `summarize_push`, the `URLNotAllowedError` branch).

**Step 2: Format + lint + security**

Run:
```bash
uv run black apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py
uv run ruff check apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py --fix
uv run bandit -r apps/alerts/management/commands/push_to_hub.py -c pyproject.toml
```
Expected: clean.

**Step 3: Docs**

- If `bin/AGENTS.md` or `docs/Installation.md` documents the push cron / `push.log` contents, update the description to "concise per-push summary (no payload)". Grep first: `grep -rn "push.log\|push_to_hub --json" docs/ bin/AGENTS.md`.

**Step 4: Full suite regression**

Run: `uv run pytest apps/alerts/_tests/ -q`
Expected: PASS (no regressions; confirm no other test relied on the old `"Hub accepted"` / `"Pushing N alert(s)"` wording).

**Step 5: Manual check (optional, on a host with HUB configured)**

Run: `uv run python manage.py push_to_hub` and confirm stdout is the summary block; `--json` still prints the payload; `--dry-run` unchanged.

**Step 6: Commit**

```bash
git add -A
git commit -m "docs(install): document concise push.log summary output"
```

---

## Acceptance criteria

- Default `push_to_hub` output (and cron output) is the concise timestamped summary; no payload, no secrets.
- `--json` still dumps the full payload; `--dry-run` unchanged.
- Failure paths (non-2xx, unreachable, SSRF-blocked) write a `push FAILED` summary to stderr and exit non-zero.
- 100% branch coverage on changed lines; `black`/`ruff`/`bandit`/`pytest` clean.
- No rotation and no `settings.LOGGING` change (deferred).
