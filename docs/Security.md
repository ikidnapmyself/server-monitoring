---
title: Security
layout: default
nav_order: 5
---

# Security

This document describes the security posture, configuration, and guidelines for the server monitoring system.

## Security Audit History

Every security control documented below was introduced or hardened through a tracked design/implementation pair under `docs/plans/`. These plans are the historical record — read them to understand *why* a control exists, not just *how* it behaves today.

| Date | Plan | What it added |
|------|------|---------------|
| 2026-03-17 | [`2026-03-17-security-hardening-design.md`](plans/2026-03-17-security-hardening-design.html) / [`-impl.md`](plans/2026-03-17-security-hardening-impl.html) | Baseline hardening: secret-key enforcement, debug-mode rules, security middleware stack |
| 2026-03-29 | [`2026-03-29-security-ci-design.md`](plans/2026-03-29-security-ci-design.html) | CI security workflow: `pip-audit`, `bandit`, `detect-secrets`, `trivy` |
| 2026-03-30 | [`2026-03-30-security-check-script-design.md`](plans/2026-03-30-security-check-script-design.html) / [`-implementation.md`](plans/2026-03-30-security-check-implementation.html) | `bin/check_security.sh` runtime posture check |
| 2026-04-11 | [`2026-04-11-path-traversal-prevention-design.md`](plans/2026-04-11-path-traversal-prevention-design.html) / [`-impl.md`](plans/2026-04-11-path-traversal-prevention-impl.html) | `config/security/path_traversal.py` (`resolve_safe_path`, `resolve_safe_name`, `ALLOWED_FILESYSTEM_ROOTS`) |
| 2026-04-12 | [`2026-04-12-ssrf-prevention-design.md`](plans/2026-04-12-ssrf-prevention-design.html) / [`-impl.md`](plans/2026-04-12-ssrf-prevention-impl.html) | `config/security/url_validation.py` + `http.py` (`safe_urlopen`, `validate_safe_url`, ruff `TID251` ban) |
| 2026-04-13 | [`2026-04-13-auth-enabled-by-default-design.md`](plans/2026-04-13-auth-enabled-by-default-design.html) / [`-impl.md`](plans/2026-04-13-auth-enabled-by-default-impl.html) | `API_KEY_AUTH_ENABLED=1` default; `config.W002` warning |
| 2026-04-13 | [`2026-04-13-provider-kwargs-ssrf-design.md`](plans/2026-04-13-provider-kwargs-ssrf-design.html) | `BLOCKED_CONFIG_KEYS` filter on `get_provider`/`get_active_provider` |
| 2026-04-15 | [`2026-04-15-ssti-notify-template-design.md`](plans/2026-04-15-ssti-notify-template-design.html) | SSTI protection in `apps/notify/templating.py` (`resolve_safe_name`, `ImmutableSandboxedEnvironment`, bare-Jinja rejection) |
| 2026-05-12 | [`2026-05-12-iso-27003-security-audit-notes.md`](plans/2026-05-12-iso-27003-security-audit-notes.html) | **End-to-end ISO 27001:2022 / 27003 audit pass** covering `bin/`, every `apps/*`, and `config/`. Per-module sinks, threat models, findings, sub-thresholds, and ISO Annex A control mapping. Recorded one MEDIUM finding (Finding 1, `scan_paths` config bypass) — **fixed 2026-05-13**. |

**Authoritative reference:** when writing or reviewing security-sensitive code, [`2026-05-12-iso-27003-security-audit-notes.md`](plans/2026-05-12-iso-27003-security-audit-notes.html) is the most recent end-to-end view of trust boundaries, sinks reviewed, and per-module rules. Each app's `AGENTS.md` carries the developer-facing distillation of that audit.

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
| `DJANGO_DB_PASSWORD` | Database credentials | When using MySQL/PostgreSQL |
| `CELERY_BROKER_URL` | Redis connection (may contain password) | When using Celery |

### DB-Stored Secrets

API keys and credentials for notification channels and intelligence providers are stored in database JSON fields:

- `NotificationChannel.config` — webhook URLs, SMTP passwords, API keys
- `IntelligenceProvider.config` — AI provider API keys

In production deployments:

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

## API Key Authentication

API endpoints require authentication via API key for non-GET requests. Keys are managed via the Django admin.

### Setup

1. API key authentication is **enabled by default**. To disable for local development, set `API_KEY_AUTH_ENABLED=0`.
2. Create a key via admin (`/admin/config_app/apikey/`) or shell:

```python
from config.models import APIKey
key = APIKey.objects.create(name="my-client")
print(key.key)  # 40-char hex token
```

### Usage

Include the key in every request via one of:

```
Authorization: Bearer <key>
X-API-Key: <key>
```

### Endpoint Restrictions

Keys can optionally restrict access to specific path prefixes via the `allowed_endpoints` JSON field (e.g., `["/alerts/"]`). Empty list = access all API endpoints.

### Exempt Paths

The following paths do **not** require an API key:

- `/alerts/webhook/` — `GET` health check for the webhook endpoint
- `/intelligence/health/` — `GET` service health status
- `/admin/*` — Django session auth (not API key auth)
- `/static/*`

All other `GET` and `POST` requests on API paths (`/alerts/`, `/orchestration/`, `/notify/`, `/intelligence/`) require a valid key. In particular, data-returning endpoints such as `/orchestration/pipelines/`, `/intelligence/providers/`, and `/intelligence/recommendations/` are **not** exempt.

The two health-check paths (`/alerts/webhook/`, `/intelligence/health/`) use an exact-equality match (`path in HEALTH_CHECK_PATHS`), so a suffix like `/alerts/webhook/data` does not bypass auth. The admin and static prefixes use a `startswith` match; both assume admin lives at the default `/admin/` path. **If `ROOT_URLCONF` ever relocates admin to a non-default path, `EXEMPT_PATH_PREFIXES` (`config/middleware/constants.py`) must be updated in lockstep.**

### API Key Disclosure Model

`APIKey.prefix` stores the first 8 hex characters of the raw key for safe admin display (`{prefix}***` in the list view). Remaining entropy after prefix disclosure is 128 bits — well outside brute-force range — but treat the admin list view as a trust-bearing surface (screenshots, logs).

The raw key itself is **never** persisted: only its SHA-256 digest is stored (`APIKey.key`, 64 hex chars) and the digest field is `editable=False`. Operators see the raw key exactly once at creation time.

## Webhook Signature Verification

Drivers support opt-in HMAC signature verification. When a secret is configured for a driver, incoming webhooks must include a valid signature.

### Configuration

Set an environment variable per driver:

| Variable | Driver |
|----------|--------|
| `WEBHOOK_SECRET_GRAFANA` | Grafana |
| `WEBHOOK_SECRET_PAGERDUTY` | PagerDuty |
| `WEBHOOK_SECRET_NEWRELIC` | New Relic |
| `WEBHOOK_SECRET_GENERIC` | Generic webhook |

Drivers without native signature support (Alertmanager, Datadog, OpsGenie, Zabbix) do not perform verification.

### How It Works

- The driver declares its signature header (e.g., `X-Grafana-Signature`)
- On incoming POST, if the env var is set, the middleware computes `HMAC-SHA256(secret, request.body)` and compares with the header value using `hmac.compare_digest` (constant-time)
- Missing or invalid signature → `403 Forbidden`
- No env var configured → verification skipped (opt-in)

### Auto-Detection Fallback

When `driver=` is omitted from the request, the alerts ingestor probes drivers in registry order and uses the first that successfully `validate()`s the payload. If the matched driver has **no** `WEBHOOK_SECRET_<NAME>` env var configured, HMAC verification is silently skipped — this is the documented opt-in model.

**Operator rule:** for any inbound driver you trust in production, set the corresponding `WEBHOOK_SECRET_*` env var. Auto-detect plus an unset secret means the endpoint is reachable by any caller who can hit it; combine with `APIKey.allowed_endpoints` and rate-limiting to bound the surface.

## Rate Limiting

Application-level rate limiting using Django cache with fixed-window counters (one bucket per UTC minute per identity/prefix).

### Configuration

Enable: `RATE_LIMIT_ENABLED=1`

Default limits (configurable via `RATE_LIMITS` in settings):

| Path prefix | Limit |
|-------------|-------|
| `/alerts/` | 120 req/min |
| `/orchestration/` | 30 req/min |
| `/notify/` | 30 req/min |
| `/intelligence/` | 20 req/min |

### Identity

Limits are tracked per API key name (if authenticated) or per client IP (`REMOTE_ADDR`).

**Reverse-proxy caveat:** `RateLimitMiddleware._get_identity` reads `request.META["REMOTE_ADDR"]` directly. Behind a proxy (nginx, Cloudflare, ALB), `REMOTE_ADDR` is the proxy's IP — every external client shares one bucket and the limiter becomes a global throttle. When deploying behind a proxy, either (a) configure a proxy-aware `REMOTE_ADDR` setter middleware **before** `RateLimitMiddleware`, or (b) rely solely on per-API-key bucketing (named keys side-step the IP collapse). Most operational deployments fall into (b).

### Scope: Mutating Requests Only

`GET` requests are exempt from rate limiting (`RateLimitMiddleware.__call__` line 41). Read endpoints like `/orchestration/pipelines/`, `/notify/channels/`, `/intelligence/providers/` are not rate-paced by this middleware. Per-API-key `allowed_endpoints` already gate which read endpoints a key can hit; if you need read-side pacing (e.g., to bound enumeration cost), terminate that at the upstream proxy.

### Cache Backend

Rate limiting requires a shared cache backend (Redis or Memcached) in multi-process deployments. A Django system check (`config.W001`) warns if rate limiting is enabled with `LocMemCache`.

### Current Limitations

The following are known areas for improvement:

- **DB-stored secrets not encrypted** — API keys and provider credentials in JSON fields are not encrypted at rest. Consider field-level encryption for high-security environments.

## Path Traversal Protection

All user-supplied file and directory paths (HTTP query parameters, CLI arguments) must be resolved to absolute form before use. This prevents path traversal attacks where an attacker submits paths like `../../etc/shadow` to access sensitive system files.

### Centralized Utility

Path traversal prevention is centralized in `config/security/path_traversal.py`. The package is organized by attack type so future security checks (injection, secret redaction, etc.) slot in naturally.

```
config/security/
  __init__.py              # Re-exports all public APIs
  path_traversal.py        # Path traversal prevention
```

### API

**`resolve_safe_path(user_input, allowed_roots)`** — Resolves a path to absolute form via `pathlib.Path.resolve()` and validates it against an allowlist. Raises `PathNotAllowedError` if outside all allowed roots.

```python
from config.security import resolve_safe_path, PathNotAllowedError

# In a Django view:
try:
    path = resolve_safe_path(request.GET.get("path", "/var/log"))
except PathNotAllowedError as e:
    return error_response(str(e), status=400)

# In a management command:
try:
    path = resolve_safe_path(options["path"])
except PathNotAllowedError as e:
    raise CommandError(str(e))
```

**`resolve_safe_name(name)`** — Validates a filename or template name. Rejects names containing slashes, backslashes, leading dots, or `..` sequences. Used for template loading where names come from DB config.

```python
from config.security import resolve_safe_name, PathNotAllowedError

try:
    name = resolve_safe_name(template_name)
except PathNotAllowedError:
    return None  # treat as "template not found"
```

**Default allowlist** (`ALLOWED_FILESYSTEM_ROOTS`): `/var`, `/tmp`, `/home`, `/opt`, `/srv`, `/usr` (resolved at import time to handle OS symlinks like macOS `/tmp` -> `/private/tmp`). The root path `/` is intentionally excluded — if included, any path would pass validation.

### Protected Entry Points

| Entry Point | Protection | Error Handling |
|-------------|-----------|----------------|
| `GET /intelligence/disk/?path=...` | `resolve_safe_path()` | 400 JSON error |
| `intelligence/providers/local.py` subprocess | `resolve_safe_path()` | Propagates to caller |
| `notify/templating.py` template loading | `resolve_safe_name()` | Returns None |
| `get_recommendations --path` | `resolve_safe_path()` | CommandError |
| `run_pipeline --file`, `--config` | `resolve_safe_path()` | CommandError |
| `check_health --disk-paths` | `resolve_safe_path()` | CommandError |
| `run_check --paths` | `resolve_safe_path()` | CommandError |

### Rules for New Code

- **Always use the utility**: Import from `config.security`, do not write inline validation
- **Resolve before use**: Never pass user-supplied paths directly to file operations or subprocess calls
- **Full executable path in subprocess argv**: Resolve the binary via `shutil.which("toolname")` and pass the absolute result as `argv[0]`. Bare-name PATH lookups at exec time are forbidden — they let an attacker-controlled PATH steer the call. Pair with `# nosec B603  # nosemgrep` on the `subprocess.Popen` line so the static analyzers accept the (resolved) dynamic argv as intentional.
- **No `/` in allowlists**: Including `/` makes the allowlist meaningless
- **Handle defaults explicitly**: If a command defaults to `/`, skip validation for that specific default value
- **Filter path-bearing config kwargs:** any provider/driver kwarg accepting a host, URL, filesystem path, command, or template name **must** be added to the relevant allowlist-by-omission filter (`apps.intelligence.providers.BLOCKED_CONFIG_KEYS`, `apps.orchestration.executors._PAYLOAD_TEMPLATE_KEYS`) **or** validated at the constructor via `resolve_safe_path` / `validate_safe_url`. See [`Finding 1`](plans/2026-05-12-iso-27003-security-audit-notes.html#finding-1--path-traversal--information-disclosure-via-scan_paths-config-medium-confidence-810--fixed-2026-05-13) in the ISO 27003 audit for the worked example (`scan_paths` bypass).

### Pipeline Stat-Only Sinks

`apps/intelligence/providers/local.py` walks the configured `scan_paths` via `Path.rglob("*")` and reports filename / size / mtime metadata only — no file contents are returned. The path validation contract still requires every entry in `scan_paths` to originate from admin-controlled DB config (the API caller cannot supply `scan_paths` because it is in `BLOCKED_CONFIG_KEYS`). Symlink following is the default behaviour of `rglob`, which compounds the impact of any path-validation gap; this is intentional for cleanup-recommendation use cases but means any future code reading file *contents* from these walks must add per-entry `resolve_safe_path` validation.

### Resolve-vs-Open TOCTOU

`Path.resolve()` validates the path at validation time. If the path is replaced with a symlink (pointing out-of-tree) *after* `resolve_safe_path` returns but *before* the caller `open()`s it, the open follows the new symlink. Defending against this requires `os.open(path, O_NOFOLLOW)` or `os.O_PATH`-based fd handling — neither is currently used. In single-user / single-tenant deployments the attacker would already need filesystem-write access to exploit; for shared-host or multi-tenant deployments this becomes a real concern and should be hardened before deploying.

### Allowlist Hygiene for Operators

`ALLOWED_FILESYSTEM_ROOTS = (/var, /tmp, /home, /opt, /srv, /usr)` is the default. The `/home` entry covers **all** user home directories; for a multi-user host you should narrow this to `/home/<service-user>` either by overriding the constant or by setting tighter custom roots in the calling code. `/tmp` is world-writable; rely on per-process tempdirs (Python `tempfile`) when storing sensitive data.

## SSRF Prevention

All outbound HTTP requests must be validated against private/reserved IP ranges before execution. This prevents Server-Side Request Forgery (SSRF) attacks where an attacker-controlled URL redirects the server to internal services, cloud metadata endpoints, or loopback addresses.

### Centralized Utility

SSRF prevention is centralized in `config/security/`, following the same pattern as path traversal prevention:

```
config/security/
  __init__.py              # Re-exports all public APIs
  path_traversal.py        # Path traversal prevention
  url_validation.py        # SSRF URL/IP validation
  http.py                  # safe_urlopen wrapper
```

### API

**`safe_urlopen(request, *, allowed_hosts, timeout)`** — Drop-in replacement for `urllib.request.urlopen`. Validates the request URL against private/reserved IP ranges before making the HTTP request. Use this in all application code instead of raw `urlopen`.

```python
from config.security.http import safe_urlopen

# In a notify driver — replaces urllib.request.urlopen:
with safe_urlopen(request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=30) as response:
    response_body = response.read().decode("utf-8")
```

**`validate_safe_url(url, allowed_hosts)`** — Low-level validator for URLs passed to third-party SDK constructors (ollama, openai) that have their own HTTP stacks. Parses the URL, resolves the hostname via DNS, and rejects any URL whose resolved IP falls in private, loopback, link-local, reserved, or multicast ranges.

```python
from config.security import validate_safe_url, URLNotAllowedError

# In a provider __init__:
validate_safe_url(base_url, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
```

### Configuration

`SSRF_ALLOWED_HOSTS` — comma-separated list of hostnames/IPs that bypass the private-IP check. Default: empty (no exceptions).

```bash
# Allow Ollama on local network and internal hub
SSRF_ALLOWED_HOSTS=ollama.internal,10.0.1.50
```

### Enforcement

A ruff lint rule (`TID251`) bans direct `urllib.request.urlopen` imports. Violations are caught in the editor (red squiggly), at pre-commit hook time, and in CI — before code reaches production. The `config/security/http.py` wrapper is the only file exempt via `# noqa: TID251`.

### Protected Call Sites

| Call Site | Method | Error Handling |
|-----------|--------|----------------|
| `notify/drivers/generic.py` | `safe_urlopen` | Returns `{"success": False}` |
| `notify/drivers/slack.py` | `safe_urlopen` | Returns `{"success": False}` |
| `notify/drivers/pagerduty.py` | `safe_urlopen` | Returns `{"success": False}` |
| `intelligence/providers/ollama.py` | `validate_safe_url` | Raises `URLNotAllowedError` |
| `intelligence/providers/grok.py` | `validate_safe_url` | Raises `URLNotAllowedError` |
| `intelligence/providers/copilot.py` | `validate_safe_url` | Raises `URLNotAllowedError` |
| `alerts/commands/push_to_hub.py` | `safe_urlopen` | Raises `CommandError` |

### Rules for New Code

- **Use `safe_urlopen`**: For any code using `urllib.request`, import `safe_urlopen` from `config.security.http` — never use raw `urlopen`
- **Use `validate_safe_url`**: For URLs passed to third-party SDK constructors
- **Pass the allowlist**: Always pass `allowed_hosts=settings.SSRF_ALLOWED_HOSTS` so operators can configure exceptions
- **Fail closed**: If DNS resolution fails, the URL is rejected
- **Lint enforcement**: Ruff `TID251` flags any raw `urlopen` import — fix before committing

### Residual Risk: DNS Rebinding

`validate_safe_url` resolves DNS once at validation time; the actual `urlopen` later re-resolves DNS at connect time. An attacker who controls a DNS record (TTL=0) could serve a public IP on the validation lookup and a private/internal IP on the connect lookup, bypassing the private-IP check. `_SSRFRedirectHandler` closes this for redirects but **not** for the initial connection.

**In the current codebase, no API path lets the caller choose the URL** — every reachable outbound HTTP destination originates from admin-controlled DB config (`NotificationChannel.config`, `IntelligenceProvider.config`) or a hardcoded constant (PagerDuty). The practical attack therefore requires either:

1. An attacker who has compromised DNS for a hostname the admin already configured (e.g., `hooks.slack.com`), or
2. `SSRF_ALLOWED_HOSTS` to include a hostname whose DNS the attacker controls.

Both are extreme scenarios for single-tenant deployments. If the deployment ever permits caller-supplied URLs to flow into `safe_urlopen` / `validate_safe_url`, harden by either pinning the resolved IP through to connect time, or routing outbound traffic through an egress proxy that re-validates at the network layer. See the ISO 27003 audit, [`config` Sub-threshold #3](plans/2026-05-12-iso-27003-security-audit-notes.html#sub-threshold-observations-3).

### Generic Driver Response Echo

`apps/notify/drivers/generic.py` returns the remote response body back to the API caller. Documented behaviour, useful for debugging webhook integrations — but it means the SSRF allowlist (`SSRF_ALLOWED_HOSTS`) is the **gating** control, not a defense-in-depth layer. If allowlist policy ever loosens, this driver becomes a half-blind SSRF read primitive. Keep `SSRF_ALLOWED_HOSTS` narrow.

## Pipeline Orchestration

The orchestration layer (`apps.orchestration`) is the only stage controller and is the entry point for `/orchestration/pipeline/*` and `/orchestration/definitions/*/execute/`. Treat **every** field of the request body as untrusted after API-key auth: `payload`, `provider`, `provider_config`, `notify_driver`, `notify_config`, `notify_channel`, `incident_id`, `trace_id`, `checker_configs`, `labels`, etc.

### `run_id` and `trace_id`

`run_id` is **always** server-generated (`uuid.uuid4()`) in `PipelineOrchestrator.start_pipeline` and `DefinitionBasedOrchestrator.execute`. A caller-supplied `run_id` in the body is ignored. Do not introduce code paths that accept caller-chosen run IDs — attackers could collide existing records or forge `idempotency_key`s.

`trace_id` **is** caller-controllable. It is a log-correlation hint, **not** an authorization token. Never use it to gate access, identity, or routing decisions.

### `incident_id` Trust Assumption (single-tenant)

`PipelineDefinitionExecuteView` accepts `incident_id` from the request body and writes it directly onto `PipelineRun.incident_id`. Downstream stages then fetch the linked `Incident` and feed it to the AI provider / notification template. **In the current single-tenant deployment model this is not a vulnerability** — every API key has access to every incident. **In any future multi-tenant deployment** this becomes a cross-tenant information-disclosure primitive and must be re-gated with per-actor authorization.

### Pipeline Resume Authorization

`PipelineResumeView` only requires the pipeline's status to be `FAILED` or `RETRYING`. Any API key holder whose `allowed_endpoints` covers `/orchestration/pipeline/` can resume any failed pipeline. Acceptable for single-tenant; revisit before any per-tenant separation.

### `_should_skip` Discipline (definition-based pipelines)

`DefinitionBasedOrchestrator._should_skip()` supports a `skip_if_condition` string with a fixed `.has_errors` pattern matcher. **This is a fixed-pattern matcher by design.** Do not extend it into a real expression language using `eval`, `exec`, `compile`, `ast.literal_eval` over attacker data, or Jinja2 — any of those opens code-execution / SSTI on attacker-controlled `PipelineDefinition.config`. If a richer condition language is genuinely needed, route it through an explicit safe-expression parser with no name resolution and no attribute access.

## Operator Tooling (`bin/`)

The `bin/` toolchain (`install.sh`, `cli.sh`, `update.sh`, `check_security.sh`, etc.) is **admin/operator-only**. It is invoked from a shell session by a user with login access and never consumes HTTP, webhook, or task-queue input.

**Invariants for new code in `bin/`:**
- Never read or trust input that originated from the API, webhook, or Celery surface. If you need data from the Django application, run a `manage.py` command and parse its output.
- `confirm_and_run` and similar interactive helpers consume `read`/`stdin` from the operator session only.
- Subprocess spawning uses list-form argv; never interpolate user input into a shell string.
- No `sudo`, setuid, or privilege-escalation paths are introduced.

## Supply Chain

The auto-update flow in `bin/lib/update.sh` performs `git fetch origin main` and applies updates from `origin/main`. **This intentionally trusts `origin/main`** — the operator's chosen remote is the trust root for code updates. Implications:

- Anyone who can push to `origin/main` can ship code that executes with the privileges of the application user.
- Branch protection on `main` (required reviews, status checks) is the actual control.
- Operators running self-hosted clones must understand which remote they have configured.

When introducing new auto-update behaviour (signed commits, signed releases, version pinning), document the trust model alongside the change.

### Vendored `tuin` (CLI UI library)

The CLI UI depends on `tuin`, a single-file pure-bash TUI library vendored in-repo at `bin/lib/tuin.sh`. It ships and updates through the **normal reviewed `git pull origin/main` path** — branch protection on `main` is the trust root, exactly as for the rest of the code. `bin/lib/tuin_vendor.sh` exposes `vendor_tuin`, which re-fetches a tag-pinned (`v0.1.0`), unsigned copy from `raw.githubusercontent.com` **only** as a manual version-bump / corruption self-heal convenience. That fetch is atomic (temp file + `mv`) and validated by a non-empty + `Version:` marker sanity check before it replaces the vendored file, but it is **not part of the runtime trust boundary**: the committed `bin/lib/tuin.sh` is what runs, and any re-fetch must be reviewed and committed like any other change.

Deeper supply-chain hardening for the fetch itself — signed releases, published checksums, build provenance — is tracked **upstream in the [`tuin`](https://github.com/ikidnapmyself/tuin) project**, not in this repo. This note covers only the local vendoring posture.

## Production Hardening Checklist

The following are **operator-set in production** — `config/settings.py` does not impose them so that local development stays low-friction. Set these (env vars or a production settings module) before deploying with `DEBUG=0`:

```python
DJANGO_DEBUG = 0
ALLOWED_HOSTS = ["your.host.example", "..."]  # never use ['*']

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# If behind a proxy that terminates TLS:
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Additional operator tasks:

- **Rate limiting:** `RATE_LIMIT_ENABLED=1` and use Redis/Memcached cache backend (locmem warns with `config.W001`).
- **API key auth:** `API_KEY_AUTH_ENABLED=1` (default since [2026-04-13](plans/2026-04-13-auth-enabled-by-default-design.html)); `config.W002` warns if disabled in non-DEBUG.
- **SSRF allowlist:** keep `SSRF_ALLOWED_HOSTS` narrow; every entry expands the residual DNS-rebinding surface.
- **Log rotation:** the default `LOGGING` config uses a plain `FileHandler` with no rotation. Either switch to `RotatingFileHandler` / `TimedRotatingFileHandler` in your production settings, or wire `logrotate` against `LOGS_DIR/django.log`.
- **Filesystem allowlist:** narrow `ALLOWED_FILESYSTEM_ROOTS` to the directories the service genuinely needs to read; the default `/home` covers every user.
- **Reverse-proxy header trust:** if you depend on per-IP rate-limit identity, install a proxy-aware `REMOTE_ADDR` setter ahead of `RateLimitMiddleware`.
- **Branch protection on `main`:** the auto-update flow trusts `origin/main`; protect that branch.
- **Run `manage.py preflight --json` in CI** for the deployed environment; it surfaces every system check including `config.W001`, `config.W002`.

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

The security workflow runs automatically on:

- Every push to `main`
- Pull requests that change Python files, `pyproject.toml`, `uv.lock`, Docker config, or the workflow itself

**Code Security job:**

| Check | Tool | What it does |
|-------|------|-------------|
| Dependency audit | `pip-audit` | Scans installed packages for known CVEs |
| Security lint | `bandit` | Static analysis for common Python security issues |
| Secret detection | `detect-secrets` | Scans for accidentally committed credentials |

**Docker Security job:**

| Check | Tool | What it does |
|-------|------|-------------|
| Image vulnerability scan | `trivy` | Scans the Docker image for OS and library CVEs (blocks on CRITICAL **with a released fix** — `ignore-unfixed: true`) |
| HIGH vulnerability report | `trivy` | Reports HIGH-severity vulnerabilities (non-blocking) |

### Addressing Security Alerts

When a vulnerability is reported (by `pip-audit`, GitHub Dependabot, or manual audit):

1. **Identify the package and fix version** — check the CVE details for the patched version
2. **Bump the dependency:**
   - Direct dependency: update version in `pyproject.toml`, then `uv lock`
   - Transitive dependency: `uv lock --upgrade-package <package>`
3. **Verify the fix:** `uv sync --extra dev && uv run pip-audit --strict --desc`
4. **Create a PR** — the security workflow triggers automatically for dependency changes
5. **Merge promptly** — security fixes should not wait in review queues

For Docker image vulnerabilities (trivy), rebuild with an updated base image or pin a patched version of the affected OS package. The CRITICAL gate uses `ignore-unfixed: true`, so it blocks only on vulnerabilities that have a released fix; **unfixed** upstream OS CVEs (status `fix_deferred` / `affected`, no fixed version — e.g. `perl-base` CVE-2026-42496 / CVE-2026-8376) are surfaced by the non-blocking HIGH report but do not fail CI, since there is nothing to bump to. Re-evaluate once the base image ships a fix.

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

## Appendix: ISO 27001:2022 Annex A Statement of Applicability

Mapping derived from the [2026-05-12 ISO 27003 audit](plans/2026-05-12-iso-27003-security-audit-notes.html). Each control lists where the mitigation lives in the codebase.

| Control | Title | Codebase mitigation |
|---|---|---|
| A.5.15 | Access control | `config/middleware/api_key_auth.py` (per-endpoint API keys, `allowed_endpoints` allowlists); Django staff/superuser auth on `/admin/` |
| A.5.17 | Authentication information | `APIKey` model: `secrets.token_hex(20)` raw, SHA-256 digest at rest, never re-displayed; `DJANGO_SECRET_KEY` env-only with startup check; `WEBHOOK_SECRET_<DRIVER>` env vars |
| A.5.23 | Information security for use of cloud services | `validate_safe_url` on intelligence-provider base URLs; `BLOCKED_CONFIG_KEYS` filter on API-callable provider config |
| A.8.2 | Privileged access rights | `bin/` toolchain confirmed not to grant sudo or install setuid; Django admin actions gated by `is_staff` |
| A.8.3 | Information access restriction | `APIKey.allowed_endpoints` path-prefix gating; admin actions outside webhook surface |
| A.8.5 | Secure authentication | Bearer / `X-API-Key` header model; constant-time digest comparison via DB lookup on hashed digest |
| A.8.9 | Configuration management | `.env` / `.env.sample` split; `bin/check_security.sh` runtime posture check; Django system checks `config.W001`/`config.W002` |
| A.8.11 | Data masking | Pipeline stores *references* (`normalized_payload_ref`, `checker_output_ref`, `intelligence_output_ref`, `notify_output_ref`) — not raw payloads |
| A.8.12 | Data leakage prevention | Logging rules — no raw webhook payloads, tokens, or URLs in logs; `_redact_config` in `apps/intelligence/providers/base.py` |
| A.8.20 | Networks security | `validate_safe_url` private/reserved-IP allowlist on outbound HTTP destinations |
| A.8.21 | Security of network services | `safe_urlopen` with redirect re-validation; ruff `TID251` ban as compile-time gate |
| A.8.24 | Use of cryptography | `hmac.compare_digest` on webhook signature verification; SHA-256 on API key digests; Celery JSON-only serializer; no TLS bypass anywhere |
| A.8.25 | Secure development lifecycle | Ruff banned-API rule encodes the SSRF contract at lint time; SSTI regression tests under `apps/notify/_tests/`; ISO 27003 audit pass with per-module sinks review |
| A.8.26 | Application security requirements | Central `config.security` package; admin uses `format_html` placeholders consistently; allowlist-by-omission patterns (`BLOCKED_CONFIG_KEYS`, `_PAYLOAD_TEMPLATE_KEYS`, `EXEMPT_PATH_PREFIXES`) |
| A.8.28 | Secure coding | `secrets.token_hex` for key generation; `hashlib.sha256` for storage; no `mark_safe` on user data; HTML output via `format_html` placeholders; sandboxed Jinja for templates |

This table is the input for any external ISO certification or internal security review. When adding a new control surface (e.g., field-level DB encryption), add the row here and link to the implementing plan in `docs/plans/`.