# apps.alerts — Agent Notes

This file contains **app-local** guidance for working in `apps/alerts/`.

## Role in the pipeline

Stage: **ingest**

Responsibilities:
- Accept inbound alert payloads (webhooks)
- Validate + parse payloads via a driver
- Normalize into a common schema
- Create/update `Alert` + `Incident`
- Attach `trace_id/run_id` when invoked via orchestration

Output contract (to orchestrator):
- `{ incident_id, alert_fingerprint, severity, source, normalized_payload_ref }`

## Key modules

- `apps/alerts/drivers/` — payload parsers (Alertmanager, Grafana, PagerDuty, etc.)
  - Drivers should implement `validate()` and `parse()`.
- `apps/alerts/services.py` — business logic (`AlertOrchestrator`, `IncidentManager`)
- `apps/alerts/models.py` — `Alert`, `Incident`, `AlertHistory`
- `apps/alerts/urls.py` — URL routing for this app

## Boundary rules

- **Do not** call downstream stages (`apps.checkers`, `apps.intelligence`, `apps.notify`) directly.
  - Only `apps.orchestration` advances the pipeline.
- Never log/store secrets from inbound payloads. Prefer storing **redacted refs**.

## Django Admin expectations

Each app must provide an **extensive** `admin.py` so operators can manage its models and trace pipeline behavior.

For `apps.alerts`, admin should make it easy to:
- Browse incidents and linked alerts efficiently (filters, search, list displays)
- Inspect alert lifecycle/audit trail (`AlertHistory`)
- Jump from an `Incident` to related pipeline runs/stage executions (via relationships/links when available)

## App layout rules (required)

- Endpoints must live under `apps/alerts/views/` (endpoint/module-based).
  - Example: `views/webhook.py`, `views/health.py`
- Tests must live under `apps/alerts/_tests/` and mirror the module tree being tested.
  - Example: `drivers/grafana.py` → `_tests/drivers/test_grafana.py`
  - Example: `views/webhook.py` → `_tests/views/test_webhook.py`

## Doc vs code status

This repo currently still contains `views.py` and `tests.py` in some apps.
This document defines the **target layout going forward**; migrate incrementally when touching related code.
