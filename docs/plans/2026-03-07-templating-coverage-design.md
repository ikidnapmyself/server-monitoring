---
title: "2026-03-07 Templating Test Coverage Design"
parent: Plans
nav_order: 79739692
---

# Templating Test Coverage Design

## Context

`apps/notify/templating.py` is at 64% coverage. The existing tests thoroughly cover
the Jinja2 template *files* (email, slack, pagerduty, generic, incident) but barely
test the Python templating engine and service code itself.

## Changes

### 1. Add tests for `render_template` function

Cover: None/empty spec, dict specs (inline/file), auto-detect file from string,
unsupported spec type raises ValueError, file not found raises ValueError,
_SafeDict missing key fallback, Jinja2 render error propagation.

### 2. Add tests for `NotificationTemplatingService`

**`compose_incident_details`**: psutil fallbacks (mocked), psutil exception handling,
recommendation normalization (dict-of-dicts → list, single dict → list, scalar → list),
`_gb()` with None, recs_pretty formatting.

**`render_message_templates`**: config key resolution (template/text_template/payload_template),
default file candidate fallback chain, no template found raises ValueError,
HTML template from config, HTML fallback to driver_html.j2, missing HTML → None.

### 3. Mock psutil

All `compose_incident_details` tests mock psutil so they're deterministic and
CI-portable.

## Target

100% branch coverage on `apps/notify/templating.py`.