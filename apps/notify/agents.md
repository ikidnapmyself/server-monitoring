# apps.notify — Agent Notes

This file contains **app-local** guidance for working in `apps/notify/`.

## Role in the pipeline

Stage: **communicate**

Responsibilities:
- Render and dispatch notifications through configured channels/drivers
- Normalize provider responses for orchestration to persist

Output contract (to orchestrator):
- `{ deliveries: [...], provider_ids, notify_output_ref }`

## Key modules

- `apps/notify/drivers/` — notification drivers/backends
- `apps/notify/models.py` — notification channel configuration models
- `apps/notify/urls.py` — URL routing

## Boundary rules

- Avoid duplicate notifications:
  - Use **idempotency keys** for outbound provider calls where applicable.
- Always set timeouts/retries/backoff for outbound HTTP calls.
- Do not log secrets (tokens/webhook URLs). Prefer refs/redaction.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.notify`, admin should make it easy to:
- Manage `NotificationChannel` configurations safely (mask/redact secrets)
- Inspect notification attempts/deliveries for pipeline runs (typically via orchestration models)
- Correlate deliveries with `Incident` and `trace_id/run_id` to debug duplicates/failures

## App layout rules (required)

- Endpoints must live under `apps/notify/views/` (endpoint/module-based).
  - Example: `views/send.py`, `views/drivers.py`, `views/batch.py`
- Tests must live under `apps/notify/tests/` and mirror the module tree.
  - Example: `drivers/slack.py` → `tests/drivers/test_slack.py`
  - Example: `views/send.py` → `tests/views/test_send.py`

## Doc vs code status

Some code still uses `views.py` / `tests.py`. This doc defines the **target layout** going forward.
