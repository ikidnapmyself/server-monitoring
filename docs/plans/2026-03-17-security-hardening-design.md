---
title: "Security Hardening for Internet-Exposed Deployment"
parent: Plans
---

# Security Hardening Design

## Goal

Harden the application for internet-exposed deployment with three layers: API key authentication, webhook signature verification, and rate limiting.

## 1. API Key Authentication

**Model:** `APIKey` in `config/models.py`
- Fields: `key` (40-char hex, indexed, unique), `name`, `is_active`, `created_at`, `last_used_at`, `allowed_endpoints` (optional JSON)
- Registered in `config/admin.py`

**Middleware:** `config/middleware/api_key_auth.py`
- Checks `Authorization: Bearer <key>` or `X-API-Key` header
- Stateless — no session involvement for API paths
- Admin paths (`/admin/*`): skip, use existing Django session auth
- Health check GETs: exempt
- Missing/invalid key on API paths: 401
- Updates `last_used_at` on valid key, attaches `request.api_key`
- `API_KEY_AUTH_ENABLED` setting (default `True`, `False` for local dev)

## 2. Webhook Signature Verification

**On `BaseAlertDriver`:**
- `signature_header: str | None = None` — header name to check
- `signature_algorithm: str = "sha256"` — hash algorithm
- `verify_signature(request_body, header_value, secret) -> bool` — default HMAC comparison, overridable

**Secrets:** `WEBHOOK_SECRET_{DRIVER_NAME.upper()}` env var per driver.

**Flow in `AlertWebhookView.post()`:**
1. Detect driver
2. If `driver.signature_header` set AND env var exists → verify → 403 on failure
3. No header or no secret → skip (opt-in)

**Driver support:**

| Driver | Header | Notes |
|--------|--------|-------|
| Grafana | `X-Grafana-Signature` | Native support |
| PagerDuty | `X-PagerDuty-Signature` | Native support |
| NewRelic | `X-NewRelic-Signature` | Native support |
| Generic | `X-Webhook-Signature` | Custom convention |
| Alertmanager | — | No native support |
| Datadog | — | Uses API key auth |
| OpsGenie | — | IP allowlist recommended |
| Zabbix | — | No native support |

Adding signature support to a new driver = set `signature_header` on the driver class. Single file.

## 3. Rate Limiting

**Middleware:** `config/middleware/rate_limit.py`

Sliding window counter using Django cache framework. No external deps.

**Rate tiers:**

| Path prefix | Limit | Key |
|-------------|-------|-----|
| `/alerts/webhook/` | 120 req/min | per API key |
| `/orchestration/pipeline/` | 30 req/min | per API key |
| `/notify/send/`, `/notify/batch/` | 30 req/min | per API key |
| `/intelligence/` | 20 req/min | per API key |

- Cache key: `ratelimit:{api_key_name}:{path_prefix}:{minute_window}`
- Exceeded: 429 with `Retry-After` header
- Health check GETs and admin paths exempt
- `RATE_LIMIT_ENABLED` setting (default `True`)
- Django system check warns if rate limiting enabled with `LocMemCache`
- Limits configurable via `RATE_LIMITS` dict in settings

## Files

**New:**
- `config/models.py` — `APIKey` model
- `config/middleware/__init__.py`
- `config/middleware/api_key_auth.py`
- `config/middleware/rate_limit.py`
- Migration for `APIKey`

**Modified:**
- `config/settings.py` — middleware stack, new settings
- `config/admin.py` — register `APIKey`
- `apps/alerts/drivers/base.py` — add `signature_header`, `verify_signature()`
- `apps/alerts/views.py` — call signature verification after driver detection
- Per-driver files — add `signature_header` attribute where supported
- `docs/Security.md` — update documentation