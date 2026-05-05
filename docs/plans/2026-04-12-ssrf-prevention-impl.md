---
title: "SSRF Prevention Implementation Plan"
parent: Plans
---

# SSRF Prevention Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Centralize SSRF prevention into `config/security/` with a `validate_safe_url()` function and a `safe_urlopen()` wrapper, wire all 7 outbound HTTP call sites, and enforce compliance via a Django system check that bans raw `urlopen` imports.

**Architecture:** Two new modules in `config/security/`: `url_validation.py` (DNS-resolving IP check) and `http.py` (`safe_urlopen` drop-in wrapper). Notify drivers and `push_to_hub` replace `urllib.request.urlopen` with `safe_urlopen`. Intelligence providers call `validate_safe_url()` in `__init__()` since they use third-party SDK HTTP stacks. A Django system check flags any raw `urlopen` import in `apps/` to catch future code that skips the wrapper.

**Tech Stack:** Python 3.10+, `urllib.parse`, `socket`, `ipaddress` (all stdlib), Django settings + system checks, pytest

---

### Task 1: Add `SSRF_ALLOWED_HOSTS` setting

**Files:**
- Modify: `config/settings.py:234`

**Step 1: Add the setting**

Add this block after the Rate Limiting section in `config/settings.py`:

```python
# ---------------------------------------------------------------------------
# SSRF Protection
# ---------------------------------------------------------------------------
SSRF_ALLOWED_HOSTS: tuple[str, ...] = tuple(
    h.strip()
    for h in os.environ.get("SSRF_ALLOWED_HOSTS", "").split(",")
    if h.strip()
)
```

**Step 2: Verify Django starts**

Run: `uv run python manage.py check`
Expected: System check identified no issues.

**Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat(security): add SSRF_ALLOWED_HOSTS setting"
```

---

### Task 2: Create `config/security/url_validation.py` with tests (TDD)

**Files:**
- Create: `config/security/url_validation.py`
- Create: `config/_tests/security/test_url_validation.py`
- Modify: `config/security/__init__.py`

**Step 1: Write the failing tests**

Create `config/_tests/security/test_url_validation.py`:

```python
"""Tests for config.security.url_validation."""

from unittest.mock import patch

import pytest

from config.security.url_validation import URLNotAllowedError, validate_safe_url


def _mock_getaddrinfo(ip: str):
    """Return a mock getaddrinfo result resolving to the given IP."""

    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 443))]

    return _getaddrinfo


def _mock_getaddrinfo_v6(ip: str):
    """Return a mock getaddrinfo result resolving to an IPv6 address."""

    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, port or 443, 0, 0))]

    return _getaddrinfo


def _mock_getaddrinfo_multi(*ips: str):
    """Return a mock getaddrinfo with multiple resolved IPs."""

    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        results = []
        for ip in ips:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 443)))
        return results

    return _getaddrinfo


class TestValidateSafeUrl:
    """Tests for validate_safe_url()."""

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("93.184.216.34"))
    def test_public_ip_allowed(self):
        result = validate_safe_url("https://example.com/path")
        assert result == "https://example.com/path"

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("8.8.8.8"))
    def test_public_ip_literal_allowed(self):
        result = validate_safe_url("https://8.8.8.8/dns")
        assert result == "https://8.8.8.8/dns"

    # --- Scheme validation ---

    def test_ftp_scheme_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme"):
            validate_safe_url("ftp://example.com/file")

    def test_file_scheme_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme"):
            validate_safe_url("file:///etc/passwd")

    def test_no_scheme_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme"):
            validate_safe_url("example.com/path")

    def test_empty_url_rejected(self):
        with pytest.raises(URLNotAllowedError):
            validate_safe_url("")

    # --- Hostname validation ---

    def test_missing_hostname_rejected(self):
        with pytest.raises(URLNotAllowedError, match="hostname"):
            validate_safe_url("https:///path")

    # --- Private IP ranges (RFC 1918) ---

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("10.0.0.1"))
    def test_private_10_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://internal.corp/api")

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("172.16.0.1"))
    def test_private_172_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://internal.corp/api")

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("192.168.1.1"))
    def test_private_192_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://router.local/api")

    # --- Loopback ---

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1"))
    def test_loopback_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://localhost/api")

    @patch(
        "config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo_v6("::1")
    )
    def test_ipv6_loopback_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://[::1]/api")

    # --- Link-local (cloud metadata) ---

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        _mock_getaddrinfo("169.254.169.254"),
    )
    def test_link_local_metadata_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("http://169.254.169.254/latest/meta-data/")

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        _mock_getaddrinfo_v6("fe80::1"),
    )
    def test_ipv6_link_local_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://[fe80::1]/api")

    # --- Multicast ---

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        _mock_getaddrinfo("224.0.0.1"),
    )
    def test_multicast_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://multicast.local/api")

    # --- IPv6 ULA (private) ---

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        _mock_getaddrinfo_v6("fd00::1"),
    )
    def test_ipv6_ula_rejected(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://[fd00::1]/api")

    # --- DNS resolution failure (fail-closed) ---

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        side_effect=OSError("Name resolution failed"),
    )
    def test_dns_failure_rejected(self, _mock):
        with pytest.raises(URLNotAllowedError, match="resolve"):
            validate_safe_url("https://doesnotexist.invalid/api")

    # --- Dual-homed DNS (one public, one private) ---

    @patch(
        "config.security.url_validation.socket.getaddrinfo",
        _mock_getaddrinfo_multi("93.184.216.34", "10.0.0.1"),
    )
    def test_mixed_resolution_rejected(self):
        """If any resolved IP is private, the URL is rejected."""
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("https://dual-homed.example.com/api")

    # --- Allowed hosts override ---

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1"))
    def test_allowed_host_bypasses_check(self):
        result = validate_safe_url(
            "http://localhost:11434/api",
            allowed_hosts=("localhost",),
        )
        assert result == "http://localhost:11434/api"

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("10.0.1.50"))
    def test_allowed_ip_bypasses_check(self):
        result = validate_safe_url(
            "https://10.0.1.50/api",
            allowed_hosts=("10.0.1.50",),
        )
        assert result == "https://10.0.1.50/api"

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("10.0.0.1"))
    def test_non_allowed_host_still_rejected(self):
        """Allowlist for one host doesn't affect others."""
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url(
                "https://evil.internal/api",
                allowed_hosts=("safe.internal",),
            )

    # --- Port handling ---

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("8.8.8.8"))
    def test_url_with_port_allowed(self):
        result = validate_safe_url("https://example.com:8443/api")
        assert result == "https://example.com:8443/api"

    @patch("config.security.url_validation.socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1"))
    def test_url_with_port_still_checked(self):
        with pytest.raises(URLNotAllowedError, match="private"):
            validate_safe_url("http://localhost:8080/api")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest config/_tests/security/test_url_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.security.url_validation'`

**Step 3: Write the implementation**

Create `config/security/url_validation.py`:

```python
"""SSRF prevention — validate outbound URLs against private/reserved IP ranges."""

import ipaddress
import socket
import urllib.parse


class URLNotAllowedError(ValueError):
    """Raised when a URL targets a private/reserved network address."""


def validate_safe_url(
    url: str,
    allowed_hosts: tuple[str, ...] = (),
) -> str:
    """Validate that a URL does not resolve to a private/reserved IP.

    Returns the original URL if safe. Raises URLNotAllowedError if the URL
    targets a blocked address or cannot be resolved.
    """
    if not url:
        raise URLNotAllowedError("URL not allowed: empty URL")

    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise URLNotAllowedError(
            f"URL not allowed: {url!r}. scheme must be http or https, got {parsed.scheme!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise URLNotAllowedError(
            f"URL not allowed: {url!r}. hostname is missing"
        )

    # Allowlist bypass — trusted internal hosts
    if hostname in allowed_hosts:
        return url

    # Resolve DNS and check all resulting IPs
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443)
    except OSError as exc:
        raise URLNotAllowedError(
            f"URL not allowed: {url!r}. Could not resolve hostname: {exc}"
        ) from exc

    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise URLNotAllowedError(
                f"URL not allowed: {url!r}. Hostname {hostname!r} resolves to "
                f"private/reserved address {ip_str}"
            )

    return url
```

**Step 4: Update `config/security/__init__.py`**

Add the new exports alongside the existing ones:

```python
from config.security.path_traversal import (
    ALLOWED_FILESYSTEM_ROOTS,
    PathNotAllowedError,
    resolve_safe_name,
    resolve_safe_path,
)
from config.security.url_validation import URLNotAllowedError, validate_safe_url

__all__ = [
    "ALLOWED_FILESYSTEM_ROOTS",
    "PathNotAllowedError",
    "URLNotAllowedError",
    "resolve_safe_name",
    "resolve_safe_path",
    "validate_safe_url",
]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest config/_tests/security/test_url_validation.py -v`
Expected: All tests PASS

**Step 6: Run full test suite to verify no regressions**

Run: `uv run pytest --tb=short -q`
Expected: All existing tests still pass

**Step 7: Commit**

```bash
git add config/security/url_validation.py config/security/__init__.py config/_tests/security/test_url_validation.py
git commit -m "feat(security): add centralized SSRF prevention with validate_safe_url"
```

---

### Task 3: Create `config/security/http.py` — `safe_urlopen` wrapper (TDD)

**Files:**
- Create: `config/security/http.py`
- Create: `config/_tests/security/test_http.py`
- Modify: `config/security/__init__.py`

**Step 1: Write the failing tests**

Create `config/_tests/security/test_http.py`:

```python
"""Tests for config.security.http — safe_urlopen wrapper."""

import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError


class TestSafeUrlopen:
    """Tests for safe_urlopen()."""

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url", return_value="https://example.com/api")
    def test_delegates_to_urlopen_for_valid_url(self, mock_validate, mock_urlopen):
        mock_response = MagicMock()
        mock_urlopen.return_value = mock_response

        request = urllib.request.Request("https://example.com/api")
        result = safe_urlopen(request, allowed_hosts=(), timeout=15)

        mock_validate.assert_called_once_with("https://example.com/api", allowed_hosts=())
        mock_urlopen.assert_called_once_with(request, timeout=15)
        assert result is mock_response

    @patch("config.security.http.urllib.request.urlopen")
    @patch(
        "config.security.http.validate_safe_url",
        side_effect=URLNotAllowedError("private"),
    )
    def test_raises_for_private_ip(self, mock_validate, mock_urlopen):
        request = urllib.request.Request("http://169.254.169.254/meta-data/")
        with pytest.raises(URLNotAllowedError, match="private"):
            safe_urlopen(request, allowed_hosts=())

        mock_urlopen.assert_not_called()

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url", return_value="https://example.com/api")
    def test_passes_timeout_to_urlopen(self, mock_validate, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        request = urllib.request.Request("https://example.com/api")

        safe_urlopen(request, allowed_hosts=(), timeout=60)

        mock_urlopen.assert_called_once_with(request, timeout=60)

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url", return_value="https://example.com/api")
    def test_extracts_url_from_string(self, mock_validate, mock_urlopen):
        mock_urlopen.return_value = MagicMock()

        safe_urlopen("https://example.com/api", allowed_hosts=())

        mock_validate.assert_called_once_with("https://example.com/api", allowed_hosts=())

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url", return_value="http://localhost:11434/api")
    def test_passes_allowed_hosts_to_validate(self, mock_validate, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        request = urllib.request.Request("http://localhost:11434/api")

        safe_urlopen(request, allowed_hosts=("localhost",), timeout=30)

        mock_validate.assert_called_once_with(
            "http://localhost:11434/api", allowed_hosts=("localhost",)
        )

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url", return_value="https://example.com/api")
    def test_works_as_context_manager(self, mock_validate, mock_urlopen):
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        request = urllib.request.Request("https://example.com/api")
        with safe_urlopen(request, allowed_hosts=(), timeout=30) as response:
            assert response is mock_response
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest config/_tests/security/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.security.http'`

**Step 3: Write the implementation**

Create `config/security/http.py`:

```python
"""Safe HTTP client — drop-in replacement for urllib.request.urlopen with SSRF protection."""

import urllib.request

from config.security.url_validation import validate_safe_url


def safe_urlopen(request, *, allowed_hosts=(), timeout=30):
    """SSRF-safe replacement for urllib.request.urlopen.

    Validates the request URL against private/reserved IP ranges before
    making the HTTP request. Use this instead of urllib.request.urlopen
    in all application code.

    Args:
        request: A urllib.request.Request object or URL string.
        allowed_hosts: Tuple of hostnames/IPs that bypass the private-IP check.
        timeout: Request timeout in seconds.

    Returns:
        The response from urllib.request.urlopen.

    Raises:
        URLNotAllowedError: If the URL resolves to a private/reserved IP.
    """
    url = request.full_url if hasattr(request, "full_url") else str(request)
    validate_safe_url(url, allowed_hosts=allowed_hosts)
    return urllib.request.urlopen(request, timeout=timeout)
```

**Step 4: Update `config/security/__init__.py`**

Add `safe_urlopen` to the exports:

```python
from config.security.http import safe_urlopen
from config.security.path_traversal import (
    ALLOWED_FILESYSTEM_ROOTS,
    PathNotAllowedError,
    resolve_safe_name,
    resolve_safe_path,
)
from config.security.url_validation import URLNotAllowedError, validate_safe_url

__all__ = [
    "ALLOWED_FILESYSTEM_ROOTS",
    "PathNotAllowedError",
    "URLNotAllowedError",
    "resolve_safe_name",
    "resolve_safe_path",
    "safe_urlopen",
    "validate_safe_url",
]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest config/_tests/security/test_http.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add config/security/http.py config/security/__init__.py config/_tests/security/test_http.py
git commit -m "feat(security): add safe_urlopen wrapper for SSRF-protected HTTP requests"
```

---

### Task 4: Wire Generic notify driver to use `safe_urlopen`

**Files:**
- Modify: `apps/notify/drivers/generic.py`
- Modify: `apps/notify/_tests/drivers/test_generic.py`

**Step 1: Write the failing test**

Add to `apps/notify/_tests/drivers/test_generic.py`:

```python
from config.security.url_validation import URLNotAllowedError


class TestGenericDriverSSRF:
    """SSRF prevention tests for GenericNotifyDriver."""

    def test_send_rejects_ssrf_url(self):
        driver = GenericNotifyDriver()
        msg = NotificationMessage(title="test", message="body", severity="info")
        config = {"endpoint": "http://169.254.169.254/latest/meta-data/"}
        with patch(
            "apps.notify.drivers.generic.safe_urlopen",
            side_effect=URLNotAllowedError("private"),
        ):
            result = driver.send(msg, config)
            assert result["success"] is False
            assert "not allowed" in result["error"].lower() or "private" in result["error"].lower()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/notify/_tests/drivers/test_generic.py::TestGenericDriverSSRF -v`
Expected: FAIL — `safe_urlopen` not imported in driver

**Step 3: Modify the driver**

In `apps/notify/drivers/generic.py`:

Replace the `urllib.request` import and add `safe_urlopen`:

```python
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage
from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError
```

Replace `urllib.request.urlopen(request, timeout=timeout)` (line 89) with:

```python
            with safe_urlopen(
                request,
                allowed_hosts=settings.SSRF_ALLOWED_HOSTS,
                timeout=timeout,
            ) as response:
```

Add `URLNotAllowedError` catch before the existing `HTTPError` handler:

```python
        except URLNotAllowedError as e:
            return {"success": False, "error": f"URL not allowed: {e}"}
        except urllib.error.HTTPError as e:
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/notify/_tests/drivers/test_generic.py -v`
Expected: All tests PASS (new and existing)

**Step 5: Commit**

```bash
git add apps/notify/drivers/generic.py apps/notify/_tests/drivers/test_generic.py
git commit -m "fix(security): use safe_urlopen in generic notify driver"
```

---

### Task 5: Wire Slack notify driver to use `safe_urlopen`

**Files:**
- Modify: `apps/notify/drivers/slack.py`
- Modify: `apps/notify/_tests/drivers/test_slack.py`

**Step 1: Write the failing test**

Add to `apps/notify/_tests/drivers/test_slack.py`:

```python
from config.security.url_validation import URLNotAllowedError


class TestSlackDriverSSRF:
    """SSRF prevention tests for SlackNotifyDriver."""

    def test_send_rejects_ssrf_webhook(self):
        driver = SlackNotifyDriver()
        msg = NotificationMessage(title="test", message="body", severity="info")
        config = {"webhook_url": "https://hooks.slack.com/services/xxx"}
        with patch(
            "apps.notify.drivers.slack.safe_urlopen",
            side_effect=URLNotAllowedError("private"),
        ):
            result = driver.send(msg, config)
            assert result["success"] is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/notify/_tests/drivers/test_slack.py::TestSlackDriverSSRF -v`
Expected: FAIL

**Step 3: Modify the driver**

In `apps/notify/drivers/slack.py`:

Add imports:

```python
from django.conf import settings

from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError
```

Replace `urllib.request.urlopen(request, timeout=timeout)` (line 75) with:

```python
            with safe_urlopen(
                request,
                allowed_hosts=settings.SSRF_ALLOWED_HOSTS,
                timeout=timeout,
            ) as response:
```

Add `URLNotAllowedError` catch before the existing `HTTPError` handler:

```python
        except URLNotAllowedError as e:
            return {"success": False, "error": f"URL not allowed: {e}"}
        except urllib.error.HTTPError as e:
```

**Step 4: Run tests**

Run: `uv run pytest apps/notify/_tests/drivers/test_slack.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/notify/drivers/slack.py apps/notify/_tests/drivers/test_slack.py
git commit -m "fix(security): use safe_urlopen in Slack notify driver"
```

---

### Task 6: Wire PagerDuty notify driver to use `safe_urlopen`

**Files:**
- Modify: `apps/notify/drivers/pagerduty.py`
- Modify: `apps/notify/_tests/drivers/test_pagerduty.py`

**Step 1: Write the failing test**

Add to `apps/notify/_tests/drivers/test_pagerduty.py`:

```python
from config.security.url_validation import URLNotAllowedError


class TestPagerDutyDriverSSRF:
    """SSRF prevention on PagerDuty's hardcoded URL (DNS spoofing protection)."""

    def test_send_validates_api_url(self):
        """Ensure even the hardcoded URL is validated at send time."""
        driver = PagerDutyNotifyDriver()
        msg = NotificationMessage(title="test", message="body", severity="critical")
        config = {"integration_key": "a" * 32}
        with patch(
            "apps.notify.drivers.pagerduty.safe_urlopen",
            side_effect=URLNotAllowedError("DNS spoofed to private IP"),
        ):
            result = driver.send(msg, config)
            assert result["success"] is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/notify/_tests/drivers/test_pagerduty.py::TestPagerDutyDriverSSRF -v`
Expected: FAIL

**Step 3: Modify the driver**

In `apps/notify/drivers/pagerduty.py`:

Add imports:

```python
from django.conf import settings

from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError
```

Replace `urllib.request.urlopen(request, timeout=timeout)` (line 102) with:

```python
            with safe_urlopen(
                request,
                allowed_hosts=settings.SSRF_ALLOWED_HOSTS,
                timeout=timeout,
            ) as response:
```

Add `URLNotAllowedError` catch before the existing `HTTPError` handler:

```python
        except URLNotAllowedError as e:
            return {"success": False, "error": f"URL not allowed: {e}"}
        except urllib.error.HTTPError as e:
```

**Step 4: Run tests**

Run: `uv run pytest apps/notify/_tests/drivers/test_pagerduty.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/notify/drivers/pagerduty.py apps/notify/_tests/drivers/test_pagerduty.py
git commit -m "fix(security): use safe_urlopen in PagerDuty notify driver"
```

---

### Task 7: Wire intelligence providers (Ollama, Grok, Copilot)

These providers use third-party SDK HTTP stacks, so they call `validate_safe_url()` in `__init__()` rather than using `safe_urlopen`.

**Files:**
- Modify: `apps/intelligence/providers/ollama.py`
- Modify: `apps/intelligence/providers/grok.py`
- Modify: `apps/intelligence/providers/copilot.py`
- Create: `apps/intelligence/_tests/providers/test_ssrf_prevention.py`

**Step 1: Write the failing tests**

Create `apps/intelligence/_tests/providers/test_ssrf_prevention.py`:

```python
"""SSRF prevention tests for intelligence providers."""

from unittest.mock import patch

import pytest

from config.security.url_validation import URLNotAllowedError


class TestOllamaSSRF:
    def test_rejects_private_host(self):
        with patch(
            "apps.intelligence.providers.ollama.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.ollama import OllamaRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                OllamaRecommendationProvider(host="http://10.0.0.1:11434")

    def test_allows_configured_host(self):
        with patch(
            "apps.intelligence.providers.ollama.validate_safe_url",
            return_value="http://localhost:11434",
        ):
            from apps.intelligence.providers.ollama import OllamaRecommendationProvider

            provider = OllamaRecommendationProvider(host="http://localhost:11434")
            assert provider.host == "http://localhost:11434"


class TestGrokSSRF:
    def test_rejects_private_base_url(self):
        with patch(
            "apps.intelligence.providers.grok.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.grok import GrokRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                GrokRecommendationProvider(base_url="http://10.0.0.1/v1")


class TestCopilotSSRF:
    def test_rejects_private_base_url(self):
        with patch(
            "apps.intelligence.providers.copilot.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.copilot import CopilotRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                CopilotRecommendationProvider(base_url="http://10.0.0.1/api")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/intelligence/_tests/providers/test_ssrf_prevention.py -v`
Expected: FAIL — `validate_safe_url` not called in providers

**Step 3: Modify Ollama provider**

In `apps/intelligence/providers/ollama.py`, add imports and validation in `__init__`:

```python
from typing import Any

from django.conf import settings

from apps.intelligence.providers.ai_base import BaseAIProvider
from config.security.url_validation import validate_safe_url


class OllamaRecommendationProvider(BaseAIProvider):
    """Ollama intelligence provider for local LLM inference."""

    name = "ollama"
    description = "Ollama local LLM intelligence provider"
    default_model = "llama3.1"

    def __init__(
        self,
        host: str = "http://localhost:11434",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        validate_safe_url(host, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
        self.host = host
```

**Step 4: Modify Grok provider**

In `apps/intelligence/providers/grok.py`, add imports and validation:

```python
from typing import Any

from django.conf import settings

from apps.intelligence.providers.ai_base import BaseAIProvider
from config.security.url_validation import validate_safe_url


class GrokRecommendationProvider(BaseAIProvider):
    """Grok intelligence provider (OpenAI-compatible xAI endpoint)."""

    name = "grok"
    description = "Grok (xAI) intelligence provider"
    default_model = "grok-3-mini"

    def __init__(
        self,
        base_url: str = "https://api.x.ai/v1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        validate_safe_url(base_url, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
        self.base_url = base_url
        self._client = None
```

**Step 5: Modify Copilot provider**

In `apps/intelligence/providers/copilot.py`, add imports and validation:

```python
from typing import Any

from django.conf import settings

from apps.intelligence.providers.ai_base import BaseAIProvider
from config.security.url_validation import validate_safe_url


class CopilotRecommendationProvider(BaseAIProvider):
    """GitHub Copilot intelligence provider (OpenAI-compatible endpoint)."""

    name = "copilot"
    description = "GitHub Copilot intelligence provider"
    default_model = "gpt-4o"

    def __init__(
        self,
        base_url: str = "https://api.githubcopilot.com",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        validate_safe_url(base_url, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
        self.base_url = base_url
        self._client = None
```

**Step 6: Run tests to verify they pass**

Run: `uv run pytest apps/intelligence/_tests/providers/test_ssrf_prevention.py -v`
Expected: All PASS

**Step 7: Run full intelligence test suite**

Run: `uv run pytest apps/intelligence/_tests/ -v --tb=short`
Expected: All PASS

**Step 8: Commit**

```bash
git add apps/intelligence/providers/ollama.py apps/intelligence/providers/grok.py apps/intelligence/providers/copilot.py apps/intelligence/_tests/providers/test_ssrf_prevention.py
git commit -m "fix(security): add SSRF prevention to intelligence providers"
```

---

### Task 8: Wire `push_to_hub` command to use `safe_urlopen`

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py`
- Modify: `apps/alerts/_tests/commands/test_push_to_hub.py`

**Step 1: Write the failing test**

Add to `apps/alerts/_tests/commands/test_push_to_hub.py`:

```python
from config.security.url_validation import URLNotAllowedError


@patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY", MOCK_REGISTRY)
class TestPushToHubSSRF(TestCase):
    @override_settings(HUB_URL="http://10.0.0.1")
    @patch(
        "apps.alerts.management.commands.push_to_hub.safe_urlopen",
        side_effect=URLNotAllowedError("private"),
    )
    def test_private_hub_url_rejected(self, _mock_validate):
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub")
        self.assertIn("not allowed", str(ctx.exception).lower())
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py::TestPushToHubSSRF -v`
Expected: FAIL

**Step 3: Modify the command**

In `apps/alerts/management/commands/push_to_hub.py`:

Replace the `urlopen` import:

```python
from urllib.request import Request
```

(Remove `urlopen` from the import line.)

Add the safe import:

```python
from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError
```

Replace `urlopen(request, timeout=30)` (around line 115) with:

```python
            with safe_urlopen(
                request,
                allowed_hosts=settings.SSRF_ALLOWED_HOSTS,
                timeout=30,
            ) as response:
```

Remove the `open_url = urlopen` assignment (line 112) and the now-redundant `# urlopen is safe here` comment (line 111).

Add `URLNotAllowedError` handling in the except block:

```python
        except URLNotAllowedError as e:
            raise CommandError(f"HUB_URL not allowed: {e}")
        except Exception as e:
```

**Step 4: Run tests**

Run: `uv run pytest apps/alerts/_tests/commands/test_push_to_hub.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/push_to_hub.py apps/alerts/_tests/commands/test_push_to_hub.py
git commit -m "fix(security): use safe_urlopen in push_to_hub command"
```

---

### Task 9: Add ruff lint rule to ban raw `urlopen` imports

**Files:**
- Modify: `pyproject.toml`
- Modify: `config/security/http.py` (add `# noqa: TID251` to legitimate import)

**Step 1: Enable `TID` rules and add the banned-api config**

In `pyproject.toml`, add `"TID"` to the ruff lint select list:

```toml
[tool.ruff.lint]
select = ["F", "E", "W", "I", "TID"]
ignore = ["E501"]
```

Add the banned-api section after `[tool.ruff.lint.isort]`:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"urllib.request.urlopen".msg = "Use safe_urlopen from config.security.http instead for SSRF protection"
```

**Step 2: Add `noqa` to the legitimate import in `config/security/http.py`**

The wrapper itself is the only file that should import `urlopen`:

```python
import urllib.request  # noqa: TID251 — this IS the safe wrapper
```

**Step 3: Verify ruff catches violations**

Run: `uv run ruff check apps/ --select TID251`
Expected: No violations (all app code now uses `safe_urlopen`)

**Step 4: Verify ruff passes overall**

Run: `uv run ruff check .`
Expected: No issues

**Step 5: Commit**

```bash
git add pyproject.toml config/security/http.py
git commit -m "feat(security): add ruff TID251 rule banning raw urlopen imports"
```

---

### Task 10: Update documentation

**Files:**
- Modify: `docs/Security.md`
- Modify: `.env.sample`

**Step 1: Add SSRF section to Security.md**

After the "Path Traversal Protection" section (around line 259), add:

````markdown
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
````

**Step 2: Add `SSRF_ALLOWED_HOSTS` to `.env.sample`**

Add under the security section:

```bash
# SSRF Protection — comma-separated hostnames/IPs allowed to bypass private-IP check
# Example: SSRF_ALLOWED_HOSTS=ollama.internal,10.0.1.50
# SSRF_ALLOWED_HOSTS=
```

**Step 3: Commit**

```bash
git add docs/Security.md .env.sample
git commit -m "docs: add SSRF prevention section to Security.md"
```

---

### Task 11: Final verification

**Step 1: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass, zero failures

**Step 2: Run linter and formatter**

Run: `uv run black . && uv run ruff check . --fix`
Expected: No issues

**Step 3: Run Django system checks and ruff**

Run: `uv run python manage.py check && uv run ruff check .`
Expected: No issues from either tool

**Step 4: Verify test coverage**

Run: `uv run coverage run -m pytest && uv run coverage report --include="config/security/*,apps/notify/drivers/*,apps/intelligence/providers/ollama.py,apps/intelligence/providers/grok.py,apps/intelligence/providers/copilot.py,apps/alerts/management/commands/push_to_hub.py"`
Expected: 100% coverage on modified files

**Step 5: Commit any formatting fixes**

```bash
git add -A
git commit -m "style: formatting fixes from black/ruff"
```