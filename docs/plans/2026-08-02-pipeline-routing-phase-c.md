---
title: "Phase C: Durable Ingest + Inbox + Drain — Implementation Plan"
parent: Plans
---

# Phase C: Durable Ingest + Inbox + Drain (the OOM fix)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Decouple ingest from processing. The webhook **durably records the inbound alert as a `PENDING` `PipelineRun` and returns 202 immediately**; a **broker-free drain** (`manage.py process_inbox`, run as a supervised loop or cron) processes runs at a controlled rate. Floods grow a bounded DB queue instead of OOM-ing the gunicorn worker, and no Celery/Redis is required.

**Architecture (decisions locked in brainstorming — do NOT relitigate):**
- **Inbox = `PipelineRun` in a new `PROCESSING`-claimed flow.** The webhook calls `start_pipeline(payload, source)` which persists the run in `PENDING` with the payload stored on a new `inbound_payload` field. **Zero net-new top-level models** (reuses `PipelineRun` + its status machine). Inbox = `PipelineRun` rows in `PENDING`; backpressure = their count.
- **Drain = broker-free `manage.py process_inbox`.** Runs as a **supervised `--loop` systemd service** (near-real-time, restarts on crash) with a **cron/timer one-shot** fallback for hosts without systemd. Atomic claim `PENDING → PROCESSING` so overlapping drains never double-process; stale `PROCESSING` runs are reclaimed after a timeout (a crashed drain would otherwise strand a run).
- **Celery is neutralized here, not deleted.** The webhook stops enqueuing `run_pipeline_task`; `orchestration/tasks.py`, the dependency, and the compose broker stay for now and are removed in the **parked Docker/Celery-removal** work (or a tiny dedicated cleanup PR). This keeps Phase C's blast radius on routing, not a Celery teardown.
- **Scope:** only the public `AlertWebhookView` becomes durable. The internal `apps/orchestration/nodes/ingest.py` handler is **out of scope** (it belongs to the legacy `DefinitionBasedOrchestrator` graph retired in Phase D).

**Design:** `docs/plans/2026-08-01-pipeline-routing-north-star-design.md` ("Durable ingest + inbox + drain"). Builds on Phase A (#183) and Phase B (#184), both merged.

**Verified current behaviour (the north-star's "verify first"):** `AlertWebhookView` today has two non-equivalent paths — (1) `ENABLE_CELERY_ORCHESTRATION=1` + broker reachable → `run_pipeline_task.delay()` runs the **full** pipeline in a Celery worker (202 `queued`); (2) broker unreachable / flag off / eager → **sync fallback = `AlertOrchestrator.process_webhook()` = ingest only** (Alert/Incident created, **no** check/analyze/notify, 200). With Celery unused on the nodes this means alerts are either queued-but-never-drained or silently ingest-only. Phase C replaces **both** paths with durable-record + drain.

**Tech Stack:** Django 5.2, pytest, uv. **Conventions:** absolute imports; line length 100; 100% branch coverage on changed lines; TDD; one commit per task; never push to `main` (feature branch + PR).

---

## Task 0: Branch setup

```bash
git checkout main && git pull
git checkout -b feat/pipeline-routing-phase-c
```

Expected: fresh branch off main with Phases A+B present (`Incident.pipeline`, `Alert.trace_id`/`node`, `orchestrator._downstream_stages`).

---

## Task 1: `PipelineRun.inbound_payload` + `PROCESSING` status; `start_pipeline` stores the payload

**Why:** The run must carry the payload so the drain can execute it later, and needs a claimed state distinct from `PENDING`.

**Files:**
- Modify: `apps/orchestration/models.py` (`PipelineStatus` += `PROCESSING`; `PipelineRun` += `inbound_payload`)
- Modify: `apps/orchestration/orchestrator.py` (`start_pipeline` persists `inbound_payload`)
- Create: `apps/orchestration/migrations/00XX_pipelinerun_inbox.py`
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing test**

```python
def test_start_pipeline_persists_payload_and_is_pending(self):
    from apps.orchestration.models import PipelineRun, PipelineStatus
    from apps.orchestration.orchestrator import PipelineOrchestrator

    run = PipelineOrchestrator().start_pipeline(
        payload={"driver": "generic", "payload": {"k": "v"}}, source="generic"
    )
    run.refresh_from_db()
    assert run.status == PipelineStatus.PENDING
    assert run.inbound_payload == {"driver": "generic", "payload": {"k": "v"}}
```

**Step 2: Run it, expect failure** — `AttributeError`/field missing.

**Step 3: Implement**

`PipelineStatus` (add):
```python
    PROCESSING = "processing", "Processing"
```

`PipelineRun` (add near `normalized_payload_ref`):
```python
    inbound_payload = models.JSONField(
        default=dict, blank=True,
        help_text="Raw inbound payload captured at ingest so a drain can process this run.",
    )
```

`start_pipeline` — set it on create:
```python
    pipeline_run = PipelineRun.objects.create(
        trace_id=trace_id, run_id=run_id, source=source, environment=environment,
        status=PipelineStatus.PENDING, max_retries=self.max_retries,
        inbound_payload=payload,
    )
```

`uv run python manage.py makemigrations orchestration`.

**Step 4: Run tests, expect PASS. Step 5: Commit** `feat(orchestration): PipelineRun carries inbound_payload; add PROCESSING status`.

> **Secrets note:** `inbound_payload` persists the raw webhook body in the DB (needed for replay). This is a durable work record, not a log — but it may contain secrets, so **retention/purge of old runs' payloads** is called out as a follow-up in Task 6's docs (not built here — YAGNI until there's a retention requirement).

---

## Task 2: Durable webhook — record a `PENDING` run and return 202 (retire both old paths)

**Why:** This is the OOM fix. The request does only bounded work (create one row) and returns; no pipeline runs inline, nothing enqueues to a broker.

**Files:**
- Modify: `apps/alerts/views.py` (`AlertWebhookView.post`)
- Test: `apps/alerts/_tests/` (the webhook view tests — rewrite to the new contract)

**Step 1: Write the failing test**

```python
def test_webhook_records_pending_run_and_returns_202(self):
    from apps.orchestration.models import PipelineRun, PipelineStatus

    resp = self.client.post(
        "/alerts/webhook/generic/",
        data=json.dumps({"name": "X", "severity": "warning"}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    run = PipelineRun.objects.get(run_id=body["run_id"])
    assert run.status == PipelineStatus.PENDING
    # No processing happened inline: no Alert yet, no stage executions.
    from apps.alerts.models import Alert
    assert Alert.objects.count() == 0
    assert run.stage_executions.count() == 0
```

**Step 2: Run it, expect failure** (today returns queued/200 and/or ingests).

**Step 3: Implement** — replace the whole Celery-enqueue + sync-fallback block (lines ~68–123) with:

```python
    # Durable ingest: record the run and return immediately. A drain
    # (manage.py process_inbox) processes it. No inline pipeline, no broker.
    from apps.orchestration.orchestrator import PipelineOrchestrator

    run = PipelineOrchestrator().start_pipeline(payload=payload, source=driver or "unknown")
    return JsonResponse(
        {"status": "accepted", "run_id": run.run_id}, status=202
    )
```

Keep the JSON-parse, driver-resolution, and `skip_checkers` injection above it unchanged (the `skip_checkers` flag rides along in `payload` and is honoured when the drain runs). Remove now-unused imports (`os`, `AlertOrchestrator`, celery-eager check).

**Step 4:** Update the other webhook-view tests that asserted `queued`/`alerts_created` in the response to the new 202 contract (this is where the old behaviour is knowingly retired — the diff *is* the verification). Ensure a driver-resolution-error still returns 400 before any run is created.

**Step 5: Run tests, expect PASS. Step 6: Commit** `feat(alerts): webhook records a PENDING run and returns 202 (durable ingest)`.

---

## Task 3: `manage.py process_inbox` — claim + execute the drain

**Why:** The broker-free worker that actually processes the queue.

**Files:**
- Create: `apps/orchestration/management/commands/process_inbox.py`
- Modify: `apps/orchestration/orchestrator.py` (thin public `execute_run(run)` wrapping `_execute_pipeline`)
- Test: `apps/orchestration/_tests/commands/test_process_inbox.py` (create)

**Claim semantics (implement exactly):**
- Claim one run atomically: `claimed = PipelineRun.objects.filter(pk=pk, status=PENDING).update(status=PROCESSING)`. Proceed only if `claimed == 1` (a concurrent drain lost the race and skips it).
- Execute via `orchestrator.execute_run(run)` (calls `_execute_pipeline(run, run.inbound_payload)`; `mark_started` is compatible with a pre-claimed run, and stages advance the status to NOTIFIED/etc. as normal). Failures already flow to `mark_failed` inside the engine.
- **Stale reclaim:** before draining, `PipelineRun.objects.filter(status=PROCESSING, updated_at__lt=now-timeout).update(status=PENDING)` so a crashed drain's run is retried (timeout default 15 min, `--stale-minutes`).

**Options:** `--limit N` (max runs per pass, default 50), `--loop` (poll forever), `--interval S` (loop sleep, default 5), `--stale-minutes M` (default 15), `--id <run_id>` (process one specific PENDING run — the "process now" escape hatch).

**Step 1: Write the failing tests** (`test_process_inbox.py`):

```python
class ProcessInboxTests(TestCase):
    def _pending(self, source="generic"):
        return PipelineOrchestrator().start_pipeline(
            payload={"driver": "generic", "payload": {"k": "v"}}, source=source
        )

    def test_drains_a_pending_run_to_completion(self):
        run = self._pending()
        call_command("process_inbox", "--limit", "10")
        run.refresh_from_db()
        assert run.status in (PipelineStatus.NOTIFIED, PipelineStatus.INGESTED, PipelineStatus.CHECKED)
        assert run.status != PipelineStatus.PENDING

    def test_claim_is_atomic_no_double_process(self):
        # A run already PROCESSING is not picked up again.
        run = self._pending()
        PipelineRun.objects.filter(pk=run.pk).update(status=PipelineStatus.PROCESSING)
        call_command("process_inbox", "--limit", "10")
        # Still PROCESSING (not re-claimed, not reset) unless stale.
        run.refresh_from_db()
        assert run.status == PipelineStatus.PROCESSING

    def test_stale_processing_is_reclaimed(self):
        run = self._pending()
        PipelineRun.objects.filter(pk=run.pk).update(status=PipelineStatus.PROCESSING)
        PipelineRun.objects.filter(pk=run.pk).update(
            updated_at=timezone.now() - timedelta(minutes=30)
        )
        call_command("process_inbox", "--limit", "10", "--stale-minutes", "15")
        run.refresh_from_db()
        assert run.status != PipelineStatus.PROCESSING  # reclaimed + processed

    def test_id_targets_one_run(self):
        r1, r2 = self._pending(), self._pending()
        call_command("process_inbox", "--id", r1.run_id)
        r1.refresh_from_db(); r2.refresh_from_db()
        assert r1.status != PipelineStatus.PENDING
        assert r2.status == PipelineStatus.PENDING

    def test_limit_bounds_the_pass(self):
        for _ in range(3):
            self._pending()
        call_command("process_inbox", "--limit", "1")
        assert PipelineRun.objects.filter(status=PipelineStatus.PENDING).count() == 2
```

> **Note on `updated_at`:** if `auto_now` prevents setting a past `updated_at` via `save`, use `.update(...)` (as above) which bypasses `auto_now`. Confirm in the test.

**Step 2: run → fail (no command). Step 3: implement the command + `execute_run`. Step 4: run → pass.**

**Step 5: Commit** `feat(orchestration): process_inbox drains PENDING runs (broker-free)`.

---

## Task 4: Backpressure — surface inbox depth in `doctor`

**Why:** The design's backpressure signal. Operators need to see the queue growing.

**Files:**
- Modify: `apps/checkers/management/commands/doctor.py` (add inbox depth + a warn check)
- Test: `apps/checkers/_tests/commands/test_doctor.py`

**Step 1: failing test**

```python
def test_doctor_reports_inbox_depth_and_warns_over_threshold(self):
    from apps.orchestration.orchestrator import PipelineOrchestrator
    for _ in range(3):
        PipelineOrchestrator().start_pipeline(payload={}, source="x")
    out = StringIO()
    call_command("doctor", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert data["inbox"]["pending"] == 3
```

**Step 2: fail. Step 3: implement** — add an `_inbox_status()` returning `{"pending": PipelineRun.objects.filter(status=PENDING).count(), "processing": ...}`, include it in the JSON + human output, and append a `WARNING` check when `pending` exceeds `getattr(settings, "INBOX_DEPTH_WARN", 500)`. **Step 4: pass. Step 5: Commit** `feat(checkers): doctor reports inbox depth + backpressure warning`.

---

## Task 5: Ops wiring + docs (supervised loop, cron fallback, Celery-neutralized note)

**Files:**
- Create: `deploy/systemd/server-monitoring-inbox.service` (a `Type=simple` unit running `manage.py process_inbox --loop --interval 5`, `Restart=always`)
- Modify: `docs/Deployment.md` (drain setup; note the webhook is now durable/202; `ENABLE_CELERY_ORCHESTRATION` no longer affects the webhook — Celery deletion deferred)
- Modify: `.env.sample` if a knob is added (e.g. `INBOX_DEPTH_WARN`)

**Content:** document (a) the supervised service (recommended, near-real-time), (b) the cron fallback `*/1 * * * * … process_inbox --limit 100`, (c) `process_inbox --id <run_id>` as the manual "process now", (d) that a flood now grows a visible `PENDING` queue (watch `doctor`) instead of OOM-ing, and (e) the eventual-consistency limit (a short queue delay under load). Commit `docs: durable ingest + process_inbox drain (systemd/cron)`.

---

## Task 6: Verify + finish branch

**Step 1: Full gate**

```bash
uv run black . --check
uv run ruff check .
uv run mypy .
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run coverage run --branch -m pytest && uv run coverage report
```

Expected: all clean; 100% branch coverage on changed lines; no pending migrations.

**Step 2: Finish** (superpowers:finishing-a-development-branch) — push, open PR to `main` with summary + test plan.

---

## Acceptance criteria ("done")

1. `POST /alerts/webhook/…` creates a `PENDING` `PipelineRun` carrying the payload and returns **202 `{status: accepted, run_id}`** without running any stage inline or enqueuing to a broker.
2. `manage.py process_inbox` drains `PENDING` runs to completion; the claim is atomic (`PENDING→PROCESSING`, no double-processing) and stale `PROCESSING` runs are reclaimed.
3. `process_inbox --loop` runs as a supervised drain; `--limit`/`--interval`/`--id`/`--stale-minutes` behave as specified; a systemd unit + cron example ship.
4. `doctor` reports inbox depth and warns above the threshold.
5. No Celery/Redis is required for the pipeline to run end-to-end; the webhook no longer references Celery (tasks.py/dependency/compose retained for the deferred cleanup).
6. All CI gates green: black, ruff, mypy, `makemigrations --check`, pytest, 100% branch coverage on changed lines.

## Out of scope (explicit)

- **Deleting Celery** (`tasks.py`, the dependency, compose broker) — deferred to the parked Docker/Celery-removal work.
- Making `apps/orchestration/nodes/ingest.py` durable — that path is retired in Phase D.
- Payload **retention/redaction/purge** — flagged in docs as a follow-up; not built until there's a requirement.
- Any external broker/queue, dedup, cross-host rate limiting, or multi-drain coordination beyond the atomic claim + stale reclaim (single-hop fan-in has no such need).
- The journey admin panel + `manage.py trace` CLI + report read model — Phase D.
