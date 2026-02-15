# Security

This document describes the security posture, configuration, and guidelines for the server monitoring system.

## Secret Management

### Django Secret Key

The `DJANGO_SECRET_KEY` environment variable is **required**. The application raises a `RuntimeError` at startup if it is not set (`config/settings.py:35`).

```bash
# Generate a production-grade key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### Environment Variables

Secrets are loaded from environment variables via `python-dotenv` (`config/env.py`). Key rules:

- **Never commit `.env` files** — only `.env.sample` is tracked
- Existing shell environment variables always take precedence (`override=False`)
- `.env.dev` is loaded only when `DJANGO_ENV=dev`

Security-sensitive variables:

| Variable | Purpose | Required |
|----------|---------|----------|
| `DJANGO_SECRET_KEY` | Django cryptographic signing | Yes (enforced) |
| `OPENAI_API_KEY` | Intelligence provider API access | When using OpenAI provider |
| `DJANGO_DB_PASSWORD` | Database credentials | When using MySQL/PostgreSQL |
| `CELERY_BROKER_URL` | Redis connection (may contain password) | When using Celery |

### Notification Channel Secrets

`NotificationChannel.config` stores driver-specific configuration (webhook URLs, API keys) in a JSON field. In production deployments:

- Use secret references rather than raw values where possible
- Restrict database access to the Django application user
- Consider encrypting sensitive fields at the application layer for high-security environments

## Django Security Configuration

### Middleware Stack

The following security middleware is enabled (`config/settings.py:64-72`):

| Middleware | Protection |
|-----------|------------|
| `SecurityMiddleware` | HTTPS redirects, HSTS, content type sniffing |
| `CsrfViewMiddleware` | Cross-site request forgery protection |
| `AuthenticationMiddleware` | Session-based authentication |
| `XFrameOptionsMiddleware` | Clickjacking protection |

### Password Validation

All four Django password validators are enabled:

- `UserAttributeSimilarityValidator`
- `MinimumLengthValidator`
- `CommonPasswordValidator`
- `NumericPasswordValidator`

### HTTPS Hardening (Production)

The following settings should be enabled in production environments via environment variables or a production settings module:

```python
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
```

### Debug Mode

`DJANGO_DEBUG` defaults to `1` (enabled) for local development. **Always set `DJANGO_DEBUG=0` in production.**

## Webhook Security

### CSRF Exemption

The webhook endpoint (`POST /alerts/webhook/`) is CSRF-exempted because external alerting systems (Grafana, Alertmanager, PagerDuty, etc.) cannot include Django CSRF tokens. This is standard practice for webhook receivers.

### Payload Validation

Each alert driver validates incoming payloads structurally via `validate()` and `parse()` methods. Drivers check for required keys and expected payload shapes.

### Current Limitations

The following are known areas for improvement:

- **No webhook signature verification** — Drivers do not verify HMAC signatures. For production deployments exposed to the internet, consider adding signature verification per provider (e.g., Grafana `X-Grafana-Signature`, PagerDuty `X-PagerDuty-Signature`).
- **No rate limiting** — The webhook endpoint has no application-level throttling. Use a reverse proxy (nginx, Cloudflare) or Django middleware for rate limiting in production.
- **No API key authentication** — Webhook endpoints accept unauthenticated requests. Consider adding bearer token or shared secret validation for production.

## Data Handling

### Redacted References

The pipeline stores **references** to data rather than raw payloads to avoid leaking secrets:

- `normalized_payload_ref` — Reference to normalized inbound payload (no raw secrets)
- `checker_output_ref` — Reference to checker output
- `intelligence_output_ref` — Reference to AI analysis (prompt/response refs, redacted)
- `notify_output_ref` — Reference to notification delivery results

### Logging

- Never log raw webhook payloads that may contain credentials
- Never log API keys, tokens, or webhook URLs
- Use structured logging with `trace_id`/`run_id` for correlation without exposing sensitive data

## Celery Security

- **JSON-only serialization** — `CELERY_ACCEPT_CONTENT`, `CELERY_TASK_SERIALIZER`, and `CELERY_RESULT_SERIALIZER` are all set to `json`, preventing pickle deserialization attacks
- Broker URL may contain credentials — treat `CELERY_BROKER_URL` as a secret

## CI Security Checks

### Automated Checks (`.github/workflows/security.yml`)

The security workflow runs on every push to `main` and on pull requests. It uses [`django-security-check`](https://github.com/victoriadrake/django-security-check) to verify Django's deployment checklist.

### CI Pipeline (`.github/workflows/ci.yml`)

- **Lint**: Black formatting + Ruff linting (catches common issues)
- **Type check**: mypy with django-stubs (catches type-related bugs)
- **Tests**: pytest across Python 3.10, 3.11, 3.12
- **Django checks**: `manage.py check` + migration consistency

## Admin Interface

The Django admin (`/admin/`) is the primary operations surface:

- Protected by Django's built-in staff/superuser authentication
- Custom `MonitoringAdminSite` inherits all default auth protections
- Admin actions (acknowledge, resolve, retry) require authenticated staff access

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly by opening a private issue or contacting the maintainer directly. Do not disclose vulnerabilities in public issues.