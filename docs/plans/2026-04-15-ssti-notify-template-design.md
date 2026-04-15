---
title: "SSTI Notify Template Fix Design"
parent: Plans
---

# Fix SSTI → RCE via `notify_config.template`

## Problem

Three independent gaps enabled RCE via an authenticated pipeline payload:

1. **Unsandboxed Jinja2 environment** at `apps/notify/templating.py:43-46` — plain `jinja2.Environment` exposes `__globals__`, `cycler`, `__class__.__mro__` — all known Jinja SSTI gadgets.
2. **Bare-string → inline source fallback** at `apps/notify/templating.py:94-111` — if a string doesn't match a known filename, it's treated as inline Jinja2 source.
3. **Pipeline payload accepts inline template** at `apps/orchestration/executors.py:390-391` — `payload_config.get("template")` comes from an untrusted POST body and flows directly into the template engine.

An authenticated attacker could POST `{"notify_config": {"template": "{{ cycler.__init__.__globals__.os.popen('id').read() }}"}}` to `/orchestration/pipeline/sync/` and execute arbitrary commands as the Django/Celery worker user.

## Solution: Defense in depth — three layers

### Layer 1: Sandboxed Jinja2 environment

```python
# apps/notify/templating.py
from jinja2.sandbox import ImmutableSandboxedEnvironment

_JINJA_ENV = ImmutableSandboxedEnvironment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
)
```

`ImmutableSandboxedEnvironment` blocks attribute access to dunder names (`__class__`, `__globals__`, `__init__`, `__mro__`, `__subclasses__`), neutralizing all known SSTI gadgets.

`autoescape` is intentionally **disabled** globally — templates are responsible for their own output escaping. All templates in `apps/notify/templates/` follow this contract:
- HTML templates (e.g. `email_html.j2`) use explicit `|e` on every user-controlled variable.
- JSON templates (Slack, PagerDuty, generic) use `|tojson` which handles JSON-safe encoding.

Enabling global autoescape would silently break Slack/text/JSON templates: any variable containing `<`, `>`, or `&` (e.g. Slack's `<url|text>` link syntax) would be HTML-entity-encoded, corrupting the rendered payload.

### Layer 2: Templating API rejects arbitrary inline strings

Change `render_template()`:

- Bare string spec: must match filename pattern (`^[\w-]+(\.\w+)?$`); treat as `file:<name>`. Anything else → `ValueError`.
- Inline source: accepted only via dict form `{"type": "inline", "template": "..."}` (staff-authored DB config continues to work this way).
- `file:` prefix: unchanged.

### Layer 3: Executor drops payload-supplied templates

Remove the `payload_config.get("template")` branch from the executor body. Additionally, strip template keys (`template`, `payload_template`, `html_template`, `text_template`) from `payload_config` before passing it to `NotifySelector.resolve()`. This ensures that when no DB channel is found and `config = payload_config`, drivers cannot pick up attacker-supplied template strings. Template sources are:

1. DB `channel_obj.config` (staff-auth gated)
2. On-disk template files under `apps/notify/templates/`

Pipeline payload callers cannot supply templates at all.

## Affected Files

| File | Change |
|------|--------|
| `apps/notify/templating.py` | Sandbox env, `autoescape=True`, reject bare-string inline |
| `apps/orchestration/executors.py:390-392` | Remove payload template branch |
| `apps/notify/_tests/test_templating.py` | SSTI regression + sandbox tests |
| `apps/orchestration/_tests/test_executors.py:738-747` | Update: payload template ignored, not rendered |

## Test Plan

- `render_template("{{ 7*7 }}", ctx)` raises `ValueError` (not a filename pattern)
- `render_template({"type": "inline", "template": "{{ ''.__class__ }}"}, ctx)` raises Jinja sandbox `SecurityError`
- `render_template({"type": "inline", "template": "Hello {{ name }}"}, {"name": "world"})` renders normally
- Payload `notify_config.template = "{{ 7*7 }}"` is ignored — default body is built instead
- Existing file-based templates still work end-to-end
- Full test suite passes

## Breaking Change Assessment

- **DB `NotificationChannel.config.template`** with bare-string inline Jinja2 would break under Layer 2. Mitigated by backcompat: bare strings that match a filename pattern are still accepted (as `file:` refs). Admins who set inline templates must migrate to dict form. No production data exists at this stage, so no migration script needed.
- **Pipeline payload `notify_config.template`** is removed entirely. Any external caller relying on this is blocked — by design, that is the vulnerability.