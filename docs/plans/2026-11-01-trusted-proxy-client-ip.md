---
title: "Trusted-proxy Client IP"
parent: Plans
---

# Trusted-proxy client IP implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Behind a configured proxy, the rate limiter buckets each client on its real address.

**Architecture:** A `TRUSTED_PROXIES` setting and a pure helper, `config/client_ip.py`, that
returns `X-Forwarded-For`'s rightmost untrusted hop when the peer is trusted, and
`REMOTE_ADDR` otherwise. `RateLimitMiddleware._get_identity` calls it. A system check refuses
entries that don't parse.

**Tech Stack:** Django 5.2, stdlib `ipaddress`, pytest-django.

Design: `docs/plans/2026-11-01-trusted-proxy-client-ip-design.md`.

Rules for every task:

- Commit with `git commit` so pre-commit runs. Messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never stage `.agent-tree/` or `docs/plans/2026-09-14-refire-staleness-open-question.md`.
  Stage files by name.
- No em or en dashes in code, docs or messages. Minimal comments.

---

### Task 1: The helper, the setting and the check

**Files:**
- Create: `config/client_ip.py`
- Modify: `config/settings.py` (next to `RATE_LIMIT_ENABLED`)
- Modify: `config/checks.py`
- Test: `config/_tests/test_client_ip.py`, `config/_tests/test_checks.py`

**Step 1: Write the failing tests**

`config/_tests/test_client_ip.py`, using `RequestFactory` and `override_settings`:

```python
import pytest
from django.test import RequestFactory, override_settings

from config.client_ip import client_ip


def _request(peer, forwarded=None):
    meta = {"REMOTE_ADDR": peer}
    if forwarded is not None:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().get("/", **meta)


@override_settings(TRUSTED_PROXIES=())
def test_with_nothing_trusted_the_header_is_ignored():
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "10.0.0.1"


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_an_untrusted_peer_cant_pick_its_address():
    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


@override_settings(TRUSTED_PROXIES=("unix",))
def test_a_unix_socket_peer_is_trusted_when_listed():
    assert client_ip(_request("", "198.51.100.7")) == "198.51.100.7"


@override_settings(TRUSTED_PROXIES=("10.0.0.1",))
def test_a_unix_socket_peer_isnt_trusted_unless_listed():
    assert client_ip(_request("", "198.51.100.7")) == ""


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8", "172.16.0.0/12"))
def test_trusted_hops_are_skipped_from_the_right():
    forwarded = "6.6.6.6, 198.51.100.7, 172.16.0.5"
    assert client_ip(_request("10.0.0.1", forwarded)) == "198.51.100.7"


@override_settings(TRUSTED_PROXIES=("::1",))
def test_ipv6_works_and_comes_back_canonical():
    assert client_ip(_request("::1", "2001:DB8::0001")) == "2001:db8::1"


@pytest.mark.parametrize("forwarded", [None, "", "   ", "10.0.0.2", "not-an-ip", "1.2.3.4, junk"])
@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_a_header_with_no_usable_client_falls_back_to_the_peer(forwarded):
    assert client_ip(_request("10.0.0.1", forwarded)) == "10.0.0.1"


@override_settings(TRUSTED_PROXIES=("bogus", "10.0.0.0/8"))
def test_a_bad_entry_is_skipped_not_raised():
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "1.2.3.4"


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_a_request_without_a_peer_address_is_unknown():
    request = RequestFactory().get("/")
    del request.META["REMOTE_ADDR"]
    assert client_ip(request) == "unknown"
```

Note `"1.2.3.4, junk"`: walking from the right, `junk` isn't an IP, so the walk stops and
the peer is returned. A trusted proxy appends the address it saw at the right, so junk there
means the header can't be trusted.

In `config/_tests/test_checks.py`, add a `TrustedProxiesCheckTests(SimpleTestCase)`:

- `TRUSTED_PROXIES=("unix", "10.0.0.1", "10.0.0.0/8", "::1")` gives no errors.
- `TRUSTED_PROXIES=("unix", "nginx", "10.0.0.0/33")` gives one `checks.Error` with
  `id="config.E001"` whose message names `nginx` and `10.0.0.0/33`.

**Step 2: Run them and watch them fail**

Run: `uv run pytest config/_tests/test_client_ip.py config/_tests/test_checks.py`
Expected: ImportError on `config.client_ip`.

**Step 3: Implement**

`config/settings.py`, below `RATE_LIMIT_ENABLED`:

```python
TRUSTED_PROXIES: tuple[str, ...] = tuple(
    p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()
)
```

`config/client_ip.py`:

```python
"""The client address behind the proxies listed in ``TRUSTED_PROXIES``."""

import ipaddress

from django.conf import settings

UNIX = "unix"


def invalid_entries(entries) -> list[str]:
    return [e for e in entries if e != UNIX and _network(e) is None]


def client_ip(request) -> str:
    entries = settings.TRUSTED_PROXIES
    peer = request.META.get("REMOTE_ADDR", "unknown")
    if not _trusted(peer, entries):
        return peer
    for hop in reversed(request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")):
        address = _address(hop.strip())
        if address is None:
            return peer
        if not _trusted(str(address), entries):
            return str(address)
    return peer


def _trusted(addr: str, entries) -> bool:
    # gunicorn reports a unix-socket peer as an empty REMOTE_ADDR.
    if addr == "":
        return UNIX in entries
    address = _address(addr)
    if address is None:
        return False
    return any(address in net for e in entries if (net := _network(e)) is not None)


def _address(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _network(entry: str):
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None
```

Adjust it until every test passes, keeping that shape. `address in net` is False across IP
versions, so mixed v4 and v6 lists need no special case.

`config/checks.py`:

```python
@checks.register()
def check_trusted_proxies(app_configs, **kwargs):
    bad = invalid_entries(getattr(settings, "TRUSTED_PROXIES", ()))
    if not bad:
        return []
    return [
        checks.Error(
            f"TRUSTED_PROXIES has entries that aren't unix, an IP or a CIDR range: "
            f"{', '.join(bad)}.",
            id="config.E001",
        )
    ]
```

**Step 4: Run them**

Run: `uv run pytest config/_tests/test_client_ip.py config/_tests/test_checks.py`
Expected: all pass.

**Step 5: Commit**

`feat(config): resolve the client IP behind trusted proxies`

---

### Task 2: The rate limiter uses it

**Files:**
- Modify: `config/middleware/rate_limit.py` (`_get_identity`)
- Test: `config/_tests/test_rate_limit_middleware.py`

**Step 1: Write the failing tests**

Keep `test_x_forwarded_for_does_not_bypass_rate_limit` as it is. The test client's peer is
`127.0.0.1` and the class leaves `TRUSTED_PROXIES` unset, so it still holds.

Add, in the same class:

- `@override_settings(TRUSTED_PROXIES=("127.0.0.1",))`: exhaust the `/alerts/` limit with
  `HTTP_X_FORWARDED_FOR="1.2.3.4"`. A request with `HTTP_X_FORWARDED_FOR="9.9.9.9"` isn't
  429, and one more with `1.2.3.4` is.
- `@override_settings(TRUSTED_PROXIES=("unix",))` with `REMOTE_ADDR=""` passed as an extra:
  the same two-client assertion.
- `@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))`: the peer `127.0.0.1` isn't
  trusted, so a different header value is still 429.

**Step 2: Run them and watch them fail**

Run: `uv run pytest config/_tests/test_rate_limit_middleware.py`
Expected: the two trusted cases get 429 for `9.9.9.9`.

**Step 3: Implement**

In `_get_identity`, replace `request.META.get("REMOTE_ADDR", "unknown")` with
`client_ip(request)`, imported from `config.client_ip`.

**Step 4: Run them**

Run: `uv run pytest config/_tests/test_rate_limit_middleware.py config/_tests/test_client_ip.py`
Expected: all pass.

**Step 5: Commit**

`feat(rate-limit): bucket each client behind a trusted proxy`

---

### Task 3: Docs

**Files:**
- Modify: `.env.sample`: a commented `TRUSTED_PROXIES` block next to the SSRF one. Say what
  it does, that `unix` means the gunicorn socket, and give `TRUSTED_PROXIES=unix` and
  `TRUSTED_PROXIES=10.0.0.0/8` as examples.
- Modify: `docs/Deployment.md`: a row in the env table after `RATE_LIMIT_ENABLED`. In "The
  Operator Console", replace the shared-IP warning with: behind the shipped nginx config set
  `TRUSTED_PROXIES=unix`, see Security.md.
- Modify: `docs/Security.md` under Rate Limiting:
  - "Sign-in pages": replace the "Behind a reverse proxy the two login buckets are shared"
    paragraph with how `TRUSTED_PROXIES` fixes it, and what happens if it's unset.
  - "Identity": per API key name, else per client IP from `config/client_ip.py`. Describe
    the rule: untrusted peer means `REMOTE_ADDR`, trusted peer means the rightmost
    untrusted `X-Forwarded-For` hop, and anything unusable falls back to `REMOTE_ADDR`.
    Warn that listing a proxy that doesn't overwrite or append `X-Forwarded-For` lets
    clients choose their bucket. Replace the "Reverse-proxy caveat" paragraph.
  - The operator-task bullet "Reverse-proxy header trust" says to set `TRUSTED_PROXIES`, and
    the preflight bullet also names `config.E001`.
- Modify: `deploy/forge/README.md`: add `TRUSTED_PROXIES=unix` to the env row.
- Modify: `deploy/systemd/` has no env template. Check `docs/Installation.md` and
  `docs/Deployment.md` for where `/etc/server-monitoring/env` is described and add
  `TRUSTED_PROXIES=unix` there.
- Modify: `deploy/docker/README.md`: a row in the env table. Set it to the platform
  proxy's address or range.

**Step 1: Edit**

**Step 2: Check the voice**

```bash
git diff origin/main -- '*.md' .env.sample | grep -nP "^\+.*(\x{2014}|\x{2013}|\bgate\b|\bseam\b|\bwire\b|leverage|crucial|navigate|\balign)"
```

Expected: no output.

**Step 3: Commit**

`docs: set TRUSTED_PROXIES behind a proxy`

---

### Task 4: Verify

**Step 1:** `uv run pytest`. Expected: all pass.

**Step 2:** `uv run coverage run -m pytest config && uv run coverage report --include="config/client_ip.py,config/checks.py,config/middleware/rate_limit.py" --show-missing`.
Expected: 100%.

**Step 3:** `uv run ruff check . && uv run black . --check && uv run mypy .`. Expected: clean.

**Step 4:** `TRUSTED_PROXIES=nginx uv run python manage.py check`. Expected: `config.E001`.
