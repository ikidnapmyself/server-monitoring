---
title: "Phase 2: Notify Content Decoupling — Implementation Plan"
parent: Plans
---

# Phase 2: Notify Content Decoupling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make notifications valuable with **no paid AI** — source the notification title and severity from the alert/check data itself, with intelligence recommendations as optional enrichment rather than the producer.

**Architecture:** Today `NotifyExecutor` derives `title` / `severity` / lead-line from AI recommendations, defaulting to `"Incident Analysis"` / `"No recommendations available."` / `info` when there are none — so a checkers-only node with a real failure sends a generic, wrongly-`info` message. The alert-derived severity already flows through `ingest["severity"]` and `Incident.title` exists. This phase adds a pure `derive_headline()` in `apps/orchestration/formatters.py`, carries `incident_title` on the ingest result, and rewires `NotifyExecutor` to use them; recommendations continue to append to the body. **Severity/title are authoritative from alert/check data; intelligence never changes severity or gates the notification.**

**Tech Stack:** Django 5.2, pytest + pytest-django, uv.

**Design doc:** `docs/plans/2026-07-26-node-model-control-plane-design.md` (§Optionality contract).

**Conventions (from AGENTS.md):** absolute imports; 100% branch coverage on changed lines; `uv run pytest`, `uv run black .`, `uv run ruff check .`. Commit after each task. TDD throughout. Follow `@superpowers:test-driven-development`.

---

## Task 1: Carry `incident_title` on the ingest result

**Files:**
- Modify: `apps/orchestration/dtos.py` (add `incident_title` to `IngestResult`)
- Modify: `apps/orchestration/executors.py` (`IngestExecutor.execute` — populate it)
- Test: `apps/orchestration/_tests/` (extend the existing IngestExecutor test module — search `class IngestExecutor` usage in `_tests`)

**Context:** `IngestExecutor` already fetches the latest alert and sets `result.incident_id` / `result.severity` from it. The incident it belongs to has a human `title` (`apps/alerts/models.py` `Incident.title`) that makes a far better notification headline than counts.

**Step 1: Write the failing test** — assert that after ingesting a cluster payload that creates an incident, the ingest result dict exposes `incident_title` equal to that incident's title. Read the existing IngestExecutor test first and mirror its setup (it builds a `StageContext` and calls `IngestExecutor().execute(ctx)`; the result is serialized to a dict via `asdict`/`to_dict` — match the established pattern).

**Step 2: Run to verify it fails** — the field does not exist yet.

**Step 3: Implementation**
- In `apps/orchestration/dtos.py`, add to the `IngestResult` dataclass: `incident_title: str = ""`.
- In `apps/orchestration/executors.py` `IngestExecutor.execute`, where it already does `if latest_alert and latest_alert.incident_id:`, also set:
  ```python
              if latest_alert.incident and latest_alert.incident.title:
                  result.incident_title = latest_alert.incident.title
  ```
  (Use `select_related("incident")` is already in place — confirm; if not, add it to avoid an extra query.)

**Step 4: Run tests** — PASS.

**Step 5: Commit**
```bash
git add apps/orchestration/dtos.py apps/orchestration/executors.py apps/orchestration/_tests
git commit -m "feat(orchestration): carry incident_title on ingest result"
```

---

## Task 2: `derive_headline()` — title + severity + lead from alert/check data

**Files:**
- Modify: `apps/orchestration/formatters.py` (add `derive_headline`)
- Test: `apps/orchestration/_tests/test_formatters.py` (create or extend — search for existing formatter tests first)

**Step 1: Write the failing test**

```python
# apps/orchestration/_tests/test_formatters.py  (add)
from apps.orchestration.formatters import derive_headline


class DeriveHeadlineTests(TestCase):
    def test_uses_incident_title_and_alert_severity(self):
        ingest = {
            "incident_title": "High CPU on web-03",
            "severity": "critical",
            "source": "cluster",
            "alerts_created": 2,
            "incidents_created": 1,
        }
        title, severity, lead = derive_headline(ingest, {})
        self.assertEqual(severity, "critical")
        self.assertIn("High CPU on web-03", title)
        self.assertIn("CRITICAL", title.upper())
        self.assertIn("2", lead)  # mentions the alert count

    def test_falls_back_when_no_incident_title(self):
        ingest = {"severity": "warning", "source": "grafana", "alerts_created": 1}
        title, severity, lead = derive_headline(ingest, {})
        self.assertEqual(severity, "warning")
        self.assertIn("grafana", title)          # source-based fallback title
        self.assertTrue(lead)

    def test_defaults_info_when_no_severity(self):
        title, severity, lead = derive_headline({}, {})
        self.assertEqual(severity, "info")
        self.assertTrue(title)

    def test_includes_failed_checks_in_lead(self):
        _, _, lead = derive_headline({"severity": "warning"}, {"checks_failed": 3})
        self.assertIn("3", lead)
```

**Step 2: Run to verify it fails** — `derive_headline` undefined.

**Step 3: Implementation** — add to `apps/orchestration/formatters.py`:

```python
def derive_headline(ingest_prev: Any, check_prev: Any) -> tuple[str, str, str]:
    """Build (title, severity, lead line) from alert/check data — never from AI.

    Severity is the alert severity carried on the ingest result (authoritative);
    the title prefers the incident title, else summarizes the source. The lead
    line summarizes what happened so a notification is useful with zero
    recommendations.
    """
    ingest = ingest_prev if isinstance(ingest_prev, dict) else {}
    check = check_prev if isinstance(check_prev, dict) else {}

    severity = (ingest.get("severity") or "info").lower()
    if severity not in ("critical", "warning", "info"):
        severity = "info"

    source = ingest.get("source") or "monitoring"
    incident_title = ingest.get("incident_title") or ""
    if incident_title:
        title = f"[{severity.upper()}] {incident_title}"
    else:
        title = f"[{severity.upper()}] {source}: incident"

    parts = []
    created = int(ingest.get("alerts_created", 0) or 0)
    updated = int(ingest.get("alerts_updated", 0) or 0)
    if created or updated:
        parts.append(f"{created + updated} alert(s) ({created} new)")
    failed = int(check.get("checks_failed", 0) or 0)
    if failed:
        parts.append(f"{failed} check(s) failed")
    lead = f"{source}: " + (", ".join(parts) if parts else "monitoring event")
    return title, severity, lead
```

**Step 4: Run tests** — PASS (all four).

**Step 5: Commit**
```bash
git add apps/orchestration/formatters.py apps/orchestration/_tests/test_formatters.py
git commit -m "feat(orchestration): derive_headline — notification title/severity from alert data"
```

---

## Task 3: Rewire `NotifyExecutor` — alert data is the source, AI enriches

**Files:**
- Modify: `apps/orchestration/executors.py` (`NotifyExecutor.execute`)
- Test: `apps/orchestration/_tests/` (the existing NotifyExecutor test module — **update tests that assert the old AI-derived defaults**)

**Context:** Replace the AI-sourced `title` / `message_body` / `severity` block (the part that sets `title = "Incident Analysis"`, `message_body = "No recommendations available."`, `severity = "info"` and overrides them from `recs`) with `derive_headline()`. Recommendations still enrich the body (they already flow into `intel_md` → `build_notification_body`). The "AI unavailable" fallback becomes a body note, **not** a title/severity override.

**Step 1: Write/adjust the failing tests**
- New test: notify with **no recommendations** but a critical ingest result → `NotificationMessage.severity == "critical"` and `title` contains the incident title (not `"Incident Analysis"`), and the body is non-empty.
- New test: notify **with** recommendations → severity still equals the alert severity (AI does not raise it), and the recommendation text appears in the body.
- Update any existing NotifyExecutor test asserting `title == "Incident Analysis"` / `severity == "info"` / `"No recommendations available."` to the new contract. Read the existing tests first.

**Step 2: Run to verify** the new tests fail against current behavior.

**Step 3: Implementation** — in `NotifyExecutor.execute`, replace the intelligence-derived `title`/`message_body`/`severity` block with:

```python
            from apps.orchestration.formatters import derive_headline

            ingest_prev = previous.get("ingest") or {}
            check_prev = previous.get("check") or {}
            title, severity, lead = derive_headline(ingest_prev, check_prev)
            message_body = lead

            # Intelligence ENRICHES the body only — it never sets severity/title.
            intelligence = previous.get("analyze", {}) or {}
            if intelligence.get("fallback_used"):
                message_body += "\n\n_AI analysis unavailable; showing check-based summary._"
```

Then leave the existing `ingest_md` / `check_md` / `intel_md` construction and
`build_notification_body(message_body, ingest_md, check_md, intel_md)` as-is —
recommendations continue to render via `intel_md`. Ensure the removed block's
later references (`intelligence`, `recs`) still resolve or are cleaned up. Keep
the template-rendering path (`render_ctx`) working; `title`/`severity` now come
from `derive_headline`.

**Step 4: Run tests** — `uv run pytest apps/orchestration/_tests/ -q` PASS.

**Step 5: Commit**
```bash
git add apps/orchestration/executors.py apps/orchestration/_tests
git commit -m "feat(notify): source title/severity from alert data; AI only enriches"
```

---

## Task 4: End-to-end optionality test (no paid AI → useful notification)

**Files:**
- Test: `apps/orchestration/_tests/test_notify_without_ai.py` (create)

**Step 1: Write the test** — drive `NotifyExecutor` (build a `StageContext` mirroring the existing NotifyExecutor tests) with `previous_results` containing a critical `ingest` (severity `"critical"`, an `incident_title`, `alerts_created`) and an `analyze` result with **no recommendations / `fallback_used=True`**, and a stub/patched driver capturing the sent `NotificationMessage`. Assert:
- `severity == "critical"` (from the alert, not `info`)
- `title` contains the incident title (not `"Incident Analysis"`)
- the message body is non-empty and mentions the alert/check summary

This is the acceptance test for "a checkers-only node with no paid AI sends a useful, correctly-severe alert." Reuse the driver-capture pattern from the existing NotifyExecutor tests (patch `DRIVER_REGISTRY` / the driver `send`).

**Step 2–4:** It should PASS given Tasks 1–3. If it fails, fix the executor (not the test).

**Step 5: Commit**
```bash
git add apps/orchestration/_tests/test_notify_without_ai.py
git commit -m "test(notify): useful, correctly-severe notification with no AI"
```

---

## Task 5: Full verification + docs

**Step 1: Run everything**
```bash
uv run black . --check
uv run ruff check .
uv run pytest -q
uv run coverage run -m pytest && uv run coverage report   # 100% on changed lines
```

**Step 2: Docs** — in `docs/Security.md` or `apps/notify/AGENTS.md` (whichever documents the notify stage), add a short note: **notification title and severity come from the alert/check data; intelligence (local or hosted) only enriches the body** — so notifications are useful and correctly severe with no AI configured.

**Step 3: Commit + finish** via `@superpowers:finishing-a-development-branch`.

---

## Notes for the executor

- **Contract:** severity/title are authoritative from alert/check data. Intelligence must never set severity or suppress a notification — only append to the body. Do not let a "helpful" tweak reintroduce AI-gated content.
- **Test churn is expected** in the existing NotifyExecutor tests — they encode the *old* coupled behavior; update them to the new contract (that is the point of the change, not a regression).
- **Coverage:** the `derive_headline` branches (incident_title present/absent, severity valid/invalid, alerts vs checks in the lead) and the `fallback_used` note branch are the easy misses — all are covered above.
