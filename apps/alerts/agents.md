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

Tests have been migrated to `_tests/` (completed). Some apps still use monolithic `views.py`; migrate to `views/` package when touching related code.

## Security standards (audit-enforced)

Authoritative source for the security threat model: [`docs/plans/2026-05-12-iso-27003-security-audit-notes.md`](../../docs/plans/2026-05-12-iso-27003-security-audit-notes.md), `apps/alerts/` section. The webhook endpoint is the **only external trust boundary** in the system — any change in this app gets audited against the rules below.

### Rules for new drivers
- **Implement `signature_header`** as a class attribute. The framework reads `WEBHOOK_SECRET_<DRIVER>` and verifies `HMAC-SHA256(secret, request.body)` against this header. Drivers without a `signature_header` get no signature verification — that is the documented opt-in fallback, but it means the endpoint is publicly reachable unless gated by API key + rate limit.
- **Use `hmac.compare_digest`** for any constant-time comparison the driver performs locally. Never use `==` on signature bytes.
- **`validate()` and `parse()` must be pure** — no DB writes, no outbound HTTP, no subprocess. Driver auto-detection probes every registered `validate()` against an unknown payload; side effects in `validate()` become reachable by any caller who can hit `/alerts/webhook/`.
- **Never `str(e)` an exception into a production error response.** Use a fixed error string; log the full exception with `logger.exception()` keyed by `trace_id`. Echoing exception messages back to the caller is an information-disclosure vector (stack details, internal paths).

### Trust boundary discipline
- Webhook payloads are the canonical **external/untrusted** input. Treat every field as attacker-supplied even after auto-detect picks a driver — auto-detection only confirms the *shape* matches a known driver, not that the *sender* is authentic.
- Never log raw payloads or signature header values; per-field logging is fine for fingerprint/severity/source.
- Stored `Alert.payload_ref` and `Incident.normalized_payload_ref` are **references**, not raw payloads.

### Audit checks before merging
- [ ] New driver added: `signature_header` declared and `WEBHOOK_SECRET_<NAME>` env var documented in `docs/Security.md`.
- [ ] No `mark_safe` / `format_html` without `{}` placeholders in admin code.
- [ ] No `str(e)` returned in HTTP response bodies.
- [ ] Run `uv run pytest apps/alerts/_tests/` and confirm signature-verification tests still pass.
