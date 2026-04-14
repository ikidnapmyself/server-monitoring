---
title: "Provider kwargs SSRF Prevention Design"
parent: Plans
---

# Strip URL kwargs from User-Supplied Provider Config

## Problem

`RecommendationsView.post()` at line 75 spreads user-controlled `provider_config` as `**kwargs` into provider constructors. For Ollama/Grok/Copilot, this controls `host`/`base_url` — direct SSRF primitives. While `validate_safe_url()` (PR #124) blocks private IPs, the user shouldn't control URL fields at all.

## Solution

Define a blocklist of URL-controlling kwargs (`host`, `base_url`) and strip them from `provider_config` in `get_provider()` before constructing the provider. This is defense-in-depth on top of the existing `validate_safe_url()` in each `__init__()`.

The filter lives in `get_provider()` itself — the centralized entry point — so any future caller that passes user-supplied kwargs also gets the protection.

Also remove `"config": provider_config` from the POST response body to avoid leaking caller-supplied config.

## Affected Files

| File | Change |
|------|--------|
| `apps/intelligence/providers/__init__.py` | Add `BLOCKED_CONFIG_KEYS`, strip in `get_provider()` |
| `apps/intelligence/views/recommendations.py:98` | Remove `"config": provider_config` from response |
| `apps/intelligence/_tests/providers/test_local.py` | Add blocked-key tests |
| `apps/intelligence/_tests/views/test_recommendations.py` | Add SSRF config test |

## Test Plan

- Test that `get_provider("ollama", host="http://evil.com")` ignores the `host` kwarg and uses the default
- Test that `get_provider("grok", base_url="http://evil.com")` ignores the `base_url` kwarg
- Test that safe kwargs (`model`, `max_tokens`) still pass through
- Test that the POST endpoint with `config: {"host": "..."}` doesn't use the supplied host
- Verify the response no longer includes the `config` field