---
title: "2026-04-12 SSRF Prevention Design"
parent: Plans
---

# Centralized SSRF Prevention

## Problem

Multiple outbound HTTP call sites across the codebase accept user-controllable URLs without validating where they resolve. An attacker (or DNS spoofing) could redirect requests to internal services, cloud metadata endpoints (169.254.169.254), or loopback addresses, leaking sensitive data or enabling lateral movement.

## Audit Findings

| Caller | URL Source | Current Validation | Risk |
|--------|-----------|-------------------|------|
| `notify/drivers/generic.py` | `config["endpoint"]` from HTTP body | Scheme prefix check only | HIGH |
| `notify/drivers/slack.py` | `config["webhook_url"]` from DB/body | None | HIGH |
| `notify/drivers/pagerduty.py` | Hardcoded constant | None (DNS spoofing) | MEDIUM |
| `intelligence/providers/ollama.py` | `host` param via `**kwargs` | None | HIGH |
| `intelligence/providers/grok.py` | `base_url` param via `**kwargs` | None | HIGH |
| `intelligence/providers/copilot.py` | `base_url` param via `**kwargs` | None | HIGH |
| `alerts/management/commands/push_to_hub.py` | `settings.HUB_URL` | Scheme prefix check only | MEDIUM |

## Solution: Centralized URL Validation

Follows the same pattern as `config/security/path_traversal.py` — a single validation function applied at every outbound HTTP call site.

### Package Structure

```
config/security/
  __init__.py              # Re-exports (add new public APIs)
  path_traversal.py        # Existing — unchanged
  url_validation.py        # NEW — SSRF IP validation
  http.py                  # NEW — safe_urlopen wrapper
config/_tests/security/
  test_path_traversal.py   # Existing — unchanged
  test_url_validation.py   # NEW — URL validation tests
  test_http.py             # NEW — safe_urlopen tests
```

### Public API

```python
class URLNotAllowedError(ValueError):
    """Raised when a URL targets a private/reserved network address."""

def validate_safe_url(
    url: str,
    allowed_hosts: tuple[str, ...] = (),
) -> str:
    """Validate that a URL does not resolve to a private/reserved IP.

    Returns the original URL if safe.
    Raises URLNotAllowedError if the URL targets a blocked address.
    """
```

### Validation Steps (Fail-Closed)

1. Parse URL with `urllib.parse.urlparse()`
2. Reject if scheme is not `http` or `https`
3. Extract hostname — reject if empty
4. Check hostname against `SSRF_ALLOWED_HOSTS` allowlist — skip IP check if matched
5. Resolve hostname via `socket.getaddrinfo()` — catch `socket.gaierror` → reject
6. Check **every** resolved IP against `ipaddress` properties:
   - `is_private` — RFC 1918, IPv6 ULA
   - `is_loopback` — 127.0.0.0/8, ::1
   - `is_link_local` — 169.254.0.0/16, fe80::/10
   - `is_reserved` — IETF reserved ranges
   - `is_multicast` — 224.0.0.0/4, ff00::/8
7. If any resolved IP is blocked → raise `URLNotAllowedError` with clear message
8. Return the original URL string unchanged

### Bypass Prevention

| Attack Vector | How Caught |
|---------------|-----------|
| Raw private IP (`http://10.0.0.1/`) | Parsed as IP, `is_private = True` |
| Domain → private IP (`evil.com` → `10.0.0.1`) | DNS resolved, IP checked |
| IPv6 loopback (`http://[::1]/`) | `is_loopback = True` |
| IPv6 mapped IPv4 (`::ffff:127.0.0.1`) | `getaddrinfo` normalizes, checked |
| Dual-homed DNS (public + private) | All resolved IPs checked, any private → blocked |
| Decimal IP encoding (`2130706433`) | `getaddrinfo` normalizes → `127.0.0.1` |
| Octal IP encoding (`0177.0.0.1`) | `getaddrinfo` normalizes → `127.0.0.1` |
| `localhost` hostname | Resolves to `127.0.0.1` → blocked |
| Unresolvable domain | `gaierror` → blocked (fail-closed) |
| Non-HTTP schemes (`file://`, `ftp://`) | Scheme check → blocked |

### Configuration

**Setting**: `SSRF_ALLOWED_HOSTS` (environment variable)
- Comma-separated list of hostnames/IPs that bypass the private-IP check
- **Default**: empty (no exceptions)
- **Example**: `SSRF_ALLOWED_HOSTS=ollama.internal,10.0.1.50`
- **Use case**: Legitimate internal services (Ollama on LAN, hub on private network)

```python
# config/settings.py
SSRF_ALLOWED_HOSTS = tuple(
    h.strip()
    for h in os.environ.get("SSRF_ALLOWED_HOSTS", "").split(",")
    if h.strip()
)
```

### Enforcement Layer: `safe_urlopen`

Rather than trusting each call site to remember `validate_safe_url()` before `urlopen()`, we provide a **drop-in replacement** that bundles validation and HTTP dispatch into one call:

```python
# config/security/http.py
def safe_urlopen(request, *, allowed_hosts=(), timeout=30):
    """Drop-in replacement for urllib.request.urlopen with SSRF protection."""
    url = request.full_url if hasattr(request, "full_url") else str(request)
    validate_safe_url(url, allowed_hosts=allowed_hosts)
    return urllib.request.urlopen(request, timeout=timeout)
```

**Before (two calls, easy to forget):**
```python
validate_safe_url(endpoint, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
urllib.request.urlopen(request, timeout=timeout)
```

**After (single enforced call):**
```python
safe_urlopen(request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=timeout)
```

All notify drivers and `push_to_hub` use `safe_urlopen` instead of `urllib.request.urlopen`. Intelligence providers still call `validate_safe_url()` directly in `__init__()` since they pass URLs to third-party SDK constructors (ollama, openai), not to `urlopen`.

### Enforcement: Ruff Banned-API Lint Rule

A `ruff` lint rule (`TID251`) bans direct `urllib.request.urlopen` imports in application code. This catches violations in the editor, at pre-commit hook time, and in CI — before code ever reaches production:

```toml
# pyproject.toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"urllib.request.urlopen".msg = "Use safe_urlopen from config.security.http instead for SSRF protection"
```

The `config/security/http.py` wrapper itself is the only file that legitimately imports `urlopen`, and is excluded via a `# noqa: TID251` comment on the import line.

### Call Site Integration

**Notify drivers** — replace `urllib.request.urlopen` with `safe_urlopen`:
```python
from config.security.http import safe_urlopen

# In send():
with safe_urlopen(request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=timeout) as response:
    ...
```

**Intelligence providers** — validate in `__init__()` (SDK has its own HTTP stack):
```python
from config.security import validate_safe_url

class OllamaRecommendationProvider(BaseAIProvider):
    def __init__(self, host="http://localhost:11434", **kwargs):
        validate_safe_url(host, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
        ...
```

**push_to_hub command** — replace `urlopen` with `safe_urlopen`:
```python
from config.security.http import safe_urlopen

with safe_urlopen(request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=30) as response:
    ...
```

### Dependencies

- **Zero new dependencies** — uses only Python stdlib: `urllib.parse`, `socket`, `ipaddress`
- No changes to `pyproject.toml`, setup scripts, or installation docs

### Test Plan

**`config/_tests/security/test_url_validation.py`** — unit tests for `validate_safe_url`:

- Public IPs → allowed
- All private ranges (10.x, 172.16.x, 192.168.x) → blocked
- Loopback (127.0.0.1, ::1, localhost) → blocked
- Link-local (169.254.x, fe80::) → blocked
- IPv6 mapped IPv4 → blocked
- Decimal/octal IP encoding → blocked
- Unresolvable domains → blocked (fail-closed)
- `SSRF_ALLOWED_HOSTS` override → allowed for listed hosts
- Scheme validation (ftp://, file://, no scheme) → blocked
- Empty/missing hostname → blocked

**`config/_tests/security/test_http.py`** — unit tests for `safe_urlopen`:

- Delegates to `validate_safe_url` before calling `urlopen`
- Raises `URLNotAllowedError` for private IPs
- Passes through to `urlopen` for valid URLs
- Forwards `timeout` parameter

**`config/_tests/test_checks.py`** — system check tests:

- Flags files with raw `urlopen` imports
- Ignores `config/security/http.py` (the wrapper itself)
- Ignores test files

### Documentation Updates

- `docs/Security.md` — add URL validation rule alongside path traversal rule
- `apps/notify/agents.md` — mention `safe_urlopen` requirement for drivers
- `apps/intelligence/agents.md` — mention `validate_safe_url` requirement for providers

### Maintenance

**Effectively zero.** The blocked IP ranges are defined by permanent RFCs. Python's `ipaddress` module tracks updates. No lists to maintain, no dependencies to patch. The Django system check enforces compliance for new code automatically.