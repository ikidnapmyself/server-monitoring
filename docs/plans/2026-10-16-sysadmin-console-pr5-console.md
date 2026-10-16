---
title: "Sysadmin Console PR 5: Console on Describers"
parent: Plans
---

# Sysadmin console PR 5: console on describers

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Overview, the incident list and the incident page answer what's wrong, where, how bad
and what to do, with fixes as actions and every identifier behind Details.

**Architecture:** This PR folds in PR 3 of the design. Orchestration, intelligence and notify
each gain a describer with a reason registry, so the console renders `Description`s and decodes
nothing. `apps/console/intents.py` resolves each `Action.intent` to a link or a POST button, or
to plain text when the user lacks the permission. One partial renders every description. A guard
test renders every page against a fixture holding each stage outcome and reason code, and fails
on any UUID, `snake_case` token, reason code or `{` outside Details.

**Tech Stack:** Django 5.2, htmx, pytest-django, dataclasses.

Design: `2026-10-15-sysadmin-console-foundation-design.md`, sections 2, 3 and 5 steps 3 and 5.

## Decisions made for this PR (2026-10-16)

- **PR 3 is folded in.** The guard test can't pass while stage text carries raw errors and
  reason codes, so the reason registries and the confidence fix ship here. Design step 3 has no
  PR of its own.
- **Config intents point at the admin for now.** `edit_channel`, `edit_route`, `edit_provider`,
  `view_node`, `view_inbox`, `view_preflight`, `view_run` and `view_alert` resolve to admin URLs
  in `intents.py`. Each later slice (PRs 6 to 9) changes its rows to console routes. No template
  or query builds an admin URL itself.
- **Buttons: incident transitions plus a local rerun.** Acknowledge, resolve and close become
  intents. `rerun_check` is a button only when the incident's instance is this machine, since
  the hub can't run a check on a peer. Otherwise it renders as text. `retry_run` waits for PR 7.

## Scope notes

- Notifications keep their current context. PR 4 rebuilds them on the describers.
- The admin keeps working on its own text. `build_readiness()` and `diagnose_incident()` keep
  their dict shapes and gain structured keys beside them, so the admin dashboard and incident
  page don't change. PR 11 deletes those consumers.
- No model or migration changes. `AnalyzeResult.confidence` becomes optional, which changes only
  the JSON stored in new snapshots.

## Conventions for every task

- Branch `feat/console-on-describers` off `main`. No worktree.
- TDD: failing test, run it, implement, run it, commit.
- 100% branch coverage on changed code:
  `uv run coverage run -m pytest <paths> && uv run coverage report --include=<files> -m`.
- Commit through pre-commit with a 600000ms timeout. Never `--no-verify`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em or en dashes in code, strings or docs. Comments only for non-obvious constraints.
- Headlines, details and action text never contain a UUID, a `snake_case` token or a `{`.

---

### Task 1: Failure classifier

One classifier for provider and driver exceptions, shared by intelligence and notify.

**Files:**
- Create: `config/failures.py`
- Test: `config/_tests/test_failures.py`

**Step 1: Write the failing tests.**

```python
import pytest

from config.failures import FAILURE_KINDS, classify_failure


class StatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class ResponseError(Exception):
    def __init__(self, status_code):
        self.response = Response(status_code)


class ReadTimeout(Exception):
    pass


@pytest.mark.parametrize(
    "exc, kind",
    [
        (StatusError(401), "auth"),
        (StatusError(403), "auth"),
        (ResponseError(429), "rate_limit"),
        (StatusError(503), "provider_down"),
        (TimeoutError(), "timeout"),
        (ReadTimeout(), "timeout"),
        (ConnectionError(), "provider_down"),
        (StatusError(400), "rejected"),
        (ValueError("boom"), "unknown"),
        (StatusError("401"), "unknown"),
    ],
)
def test_classify_failure(exc, kind):
    assert classify_failure(exc) == kind


def test_every_kind_is_listed():
    assert set(FAILURE_KINDS) == {
        "auth", "rate_limit", "timeout", "provider_down", "rejected", "unknown"
    }
```

**Step 2: Run** `uv run pytest config/_tests/test_failures.py -v`. Expected: ImportError.

**Step 3: Implement** `config/failures.py`:

```python
"""Why a call to an outside service failed, as one of a few kinds a sysadmin can act on.

SDKs disagree on exception types, so this reads the HTTP status where one is exposed and
falls back to the exception's class name.
"""

FAILURE_KINDS = ("auth", "rate_limit", "timeout", "provider_down", "rejected", "unknown")


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        code = getattr(holder, "status_code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


def classify_failure(exc: BaseException) -> str:
    status = _status(exc)
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status is not None and status >= 500:
        return "provider_down"
    if status is not None and status >= 400:
        return "rejected"
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout"
    if isinstance(exc, ConnectionError) or "Connection" in type(exc).__name__:
        return "provider_down"
    return "unknown"
```

**Step 4: Run** the test. Expected: PASS.

**Step 5: Commit** `feat(config): classify outside-service failures`.

---

### Task 2: A failed analysis is a failure

Today `AIBaseProvider.analyze` swallows the error into a fake recommendation, and
`AnalyzeExecutor` stamps `confidence = 0.8` on any non-empty result. That's how a 401 renders
as "80% confidence".

**Files:**
- Modify: `apps/intelligence/providers/ai_base.py` (`analyze`, delete `_get_fallback_recommendation`)
- Modify: `apps/orchestration/dtos.py` (`AnalyzeResult`)
- Modify: `apps/orchestration/executors.py` (`AnalyzeExecutor.execute`)
- Modify: `apps/orchestration/formatters.py` (`format_intelligence_summary`, if it reads `confidence`)
- Test: `apps/intelligence/_tests/providers/test_ai_base.py`, `apps/orchestration/_tests/test_executors.py`

**Step 1: Write the failing tests.**

- `test_analyze_raises_the_provider_error`: a subclass whose `_call_api` raises
  `StatusError(401)` makes `analyze(incident)` raise it. Delete the tests that assert the
  fallback recommendation.
- `test_analyze_failure_records_kind_and_no_confidence`: with a provider whose `run` raises
  `StatusError(401)`, `AnalyzeExecutor().execute(ctx)` returns `fallback_used=True`,
  `failure="auth"`, `error_detail` containing `"401"`, `confidence is None`,
  `recommendations == []`, `errors == []`.
- `test_analyze_success_has_no_invented_confidence`: a provider returning one recommendation
  gives `confidence is None` and `failure == ""`.

**Step 2: Run** them. Expected: FAIL.

**Step 3: Implement.**

`ai_base.py`:

```python
    def analyze(
        self, incident: Any | None = None, analysis_type: str = "", path: str = "/"
    ) -> list[Recommendation]:
        if incident is None:
            return []
        response = self._call_api(self._build_prompt(incident))
        return self._parse_response(response, incident_id=getattr(incident, "id", None))
```

`BaseProvider.run` already marks the `AnalysisRun` failed and re-raises, so nothing else in
intelligence changes.

`dtos.py`, in `AnalyzeResult`: `confidence: float | None = None`, plus
`failure: str = ""` and `error_detail: str = ""`. Update the docstring lines for both.

`executors.py`: delete `result.confidence = 0.8`. In the `except` branch:

```python
        except Exception as e:
            logger.exception("Error in AnalyzeExecutor")
            error_msg = f"Analyze error: {str(e)}"
            result.errors.append(error_msg)
            if self.fallback_enabled:
                result.fallback_used = True
                result.failure = classify_failure(e)
                result.error_detail = str(e)
                result.summary = ""
                result.probable_cause = ""
                result.actions = []
                result.errors = []
                logger.info(...)
```

Import `classify_failure` from `config.failures` at the top.

**Step 4: Run** `uv run pytest apps/intelligence apps/orchestration -q`. Fix any test that
asserted `0.8` or the fallback text. Expected: PASS.

**Step 5: Commit** `fix(intelligence)!: a failed analysis reports no confidence`.

---

### Task 3: Deliveries carry a failure kind

**Files:**
- Modify: `apps/orchestration/executors.py` (`NotifyExecutor.execute`, both delivery dicts)
- Test: `apps/orchestration/_tests/test_executors.py`

**Step 1: Failing tests.** A driver whose `send` raises `StatusError(403)` yields a delivery
with `"failure": "auth"`. A driver returning `{"success": False, "status_code": 429}` yields
`"failure": "rate_limit"`. A failed result with no status yields `"rejected"`. A success yields
`"failure": ""`.

**Step 2: Run.** Expected: FAIL with `KeyError: 'failure'`.

**Step 3: Implement.** In the `except` branch add `"failure": classify_failure(e)`. In the
`else` branch:

```python
                code = send_result.get("status_code")
                failure = "" if success else (
                    classify_failure(_Status(code)) if isinstance(code, int) else "rejected"
                )
```

with a module-level `class _Status(Exception)` holding `status_code`, so the HTTP rules live in
`classify_failure` only. Add `"failure": failure` to the dict.

**Step 4: Run.** Expected: PASS.

**Step 5: Commit** `feat(notify): record why a delivery failed`.

---

### Task 4: Orchestration describer and reason registry

**Files:**
- Create: `apps/orchestration/describing.py`
- Modify: `apps/orchestration/errors.py` (`ROUTING_GAPS` tuple)
- Test: `apps/orchestration/_tests/test_describing.py`

**Step 1: Failing tests.**

```python
import pytest

from apps.orchestration.describing import (
    REASONS,
    describe_run,
    describe_stage_outcome,
    reason_code,
)
from apps.orchestration.errors import ROUTING_GAPS


def test_every_routing_gap_has_a_reason():
    assert set(ROUTING_GAPS) <= set(REASONS)


@pytest.mark.parametrize(
    "message, code",
    [
        ("no_route: no active pipeline matched this alert", "no_route"),
        ("no_channel: the matched lane names no active notification channel", "no_channel"),
        ("Check error: boom", ""),
        ("", ""),
    ],
)
def test_reason_code(message, code):
    assert reason_code(message) == code


def test_no_channel_names_its_fix():
    d = describe_stage_outcome("notify", "failed", "no_channel: x", retryable=False)
    assert d.headline == "Not delivered: the matched route has no active channel"
    assert d.action.intent == "edit_route"
    assert d.tone == "critical"


def test_unknown_failure_keeps_the_message_in_details():
    d = describe_stage_outcome("check", "failed", "Check error: boom", retryable=True)
    assert d.headline == "Diagnose failed and will be retried"
    assert ("Error", "Check error: boom") in [(f.label, f.value) for f in d.facts]


@pytest.mark.parametrize(
    "stage, status, headline",
    [
        ("check", "succeeded", "Diagnose finished"),
        ("analyze", "skipped", "Analyze skipped"),
        ("notify", "running", "Notify is running"),
        ("notify", "pending", "Notify is waiting"),
    ],
)
def test_plain_outcomes(stage, status, headline):
    assert describe_stage_outcome(stage, status, "", retryable=False).headline == headline


def test_skip_reason_goes_into_detail():
    d = describe_stage_outcome("notify", "skipped", "Skipped: notify disabled", retryable=False)
    assert d.detail == "notify disabled"
```

`describe_run(run)` tests: a `notified` run reads "Run finished, notified", `failed` with
`last_error_message="no_route: ..."` reads "Run failed: no route matches this incident" with
`edit_route`, `pending` reads "Run queued", `processing` reads "Run in progress". `run_id` and
`trace_id` are facts with `copy=True`.

**Step 2: Run.** Expected: ImportError.

**Step 3: Implement.**

`errors.py`: `ROUTING_GAPS = ("no_route", "no_channel", "no_driver")`, and `routing_gap`
asserts `code in ROUTING_GAPS`.

`describing.py`:

```python
"""Pipeline runs and stage outcomes in words, from the status and error each row stored."""

import re
from dataclasses import dataclass

from config.describing import Action, Description, Fact

STAGE_NAMES = {"ingest": "Ingest", "check": "Diagnose", "analyze": "Analyze", "notify": "Notify"}
_CODE = re.compile(r"^([a-z]+(?:_[a-z]+)+): ")


@dataclass(frozen=True)
class Reason:
    headline: str
    action: Action


REASONS: dict[str, Reason] = {
    "no_route": Reason(
        "no route matches this incident",
        Action("Add a route or widen the catch-all", intent="edit_route"),
    ),
    "no_channel": Reason(
        "the matched route has no active channel",
        Action("Give the route an active channel", intent="edit_route"),
    ),
    "no_driver": Reason(
        "the channel uses a driver this hub doesn't have",
        Action("Change the channel's driver", intent="edit_channel"),
    ),
}

_PREFIX = {"notify": "Not delivered", "check": "Diagnose failed", "analyze": "Analyze failed"}


def reason_code(message: str) -> str:
    found = _CODE.match(message or "")
    return found.group(1) if found else ""
```

`describe_stage_outcome(stage, status, error_message, *, retryable)`:

- `failed` with a known code: headline `f"{_PREFIX.get(stage, name + ' failed')}: {reason.headline}"`,
  tone `critical`, the reason's action, the raw message as an `Error` fact.
- `failed` otherwise: `f"{name} failed and will be retried"` when `retryable`, else
  `f"{name} failed"`, tone `critical`, action `Action("Open the run's error in Details")`,
  the message as an `Error` fact.
- `skipped`: `f"{name} skipped"`, tone `muted`, detail from `error_message` with `"Skipped: "`
  removed.
- `succeeded`: `f"{name} finished"`, tone `ok`. `running`/`retrying`: `"is running"`/
  `"is retrying"`, tone `info`. `pending`: `"is waiting"`, tone `muted`.

`describe_run(run)` maps `PipelineStatus`: `pending` "Run queued", `processing`/`retrying`
"Run in progress", `checked`/`analyzed` "Run in progress, {stage} done", `notified` "Run
finished, notified", `skipped` "Run skipped", `failed` through the same reason lookup on
`run.last_error_message`. Facts: `Run`, `Trace` (both `copy=True`), `Status` as the raw value.

**Step 4: Run.** Expected: PASS.

**Step 5: Commit** `feat(orchestration): describe runs and stage outcomes`.

---

### Task 5: Intelligence describer

**Files:**
- Create: `apps/intelligence/describing.py`
- Test: `apps/intelligence/_tests/test_describing.py`

**Step 1: Failing tests** for `describe_analysis(snapshot: dict) -> Description`:

| snapshot | headline | tone | action intent |
|---|---|---|---|
| `fallback_used=True, failure="auth"` | `AI analysis failed: the provider rejected the API key` | warning | `edit_provider` |
| `failure="rate_limit"` | `...: the provider is rate limiting this hub` | warning | none, text "Wait, or raise the plan's limit" |
| `failure="timeout"` | `...: the provider didn't answer in time` | warning | none |
| `failure="provider_down"` | `...: the provider is unreachable` | warning | none |
| `failure="rejected"` | `...: the provider refused the request` | warning | `edit_provider` |
| `failure="unknown"` or missing | `AI analysis failed` | warning | `edit_provider` |
| `summary="Disk filling", probable_cause="logs"` | `Disk filling` | info | none, detail `logs` |
| empty summary, no failure | `AI analysis found nothing to add` | muted | none |

A failed analysis has no `Confidence` fact. `error_detail` is an `Error` fact. A legacy snapshot
with `confidence` and no failure gets no confidence fact either: confidence was never real.

Also: `test_every_failure_kind_is_described` loops `FAILURE_KINDS`.

**Step 2: Run.** Expected: ImportError.

**Step 3: Implement** with a `FAILURES: dict[str, tuple[str, Action]]` keyed by
`FAILURE_KINDS`, read through the `_text` style guards the console uses (the snapshot is
untrusted in shape: a list, `None` or wrong types must not raise).

**Step 4: Run.** Expected: PASS.

**Step 5: Commit** `feat(intelligence): describe analysis outcomes`.

---

### Task 6: Notify describer

**Files:**
- Create: `apps/notify/describing.py`
- Test: `apps/notify/_tests/test_describing.py`

**Step 1: Failing tests** for `describe_delivery(delivery: dict) -> Description`:

- success: `Sent to {channel} by {driver label}` (e.g. "Sent to #ops by Slack"), tone `ok`,
  `Message ID` fact with `copy=True` when present.
- failure `auth`: `Not sent to #ops: Slack rejected the credentials`, action `edit_channel`
  with `params={"name": <driver>}`.
- failure `rate_limit`, `timeout`, `provider_down`, `rejected`, `unknown`: one sentence each.
- the driver label comes from the driver registry's display name, falling back to the key
  capitalised. A missing channel reads "the default channel".

And `describe_notify(snapshot) -> tuple[Description, ...]`: one per delivery, or one
`Nothing was sent` description (tone `muted`) when there are none.

**Step 2 to 4:** as above, guarding untrusted shapes.

**Step 5: Commit** `feat(notify): describe delivery outcomes`.

---

### Task 7: Structured stage diagnosis

`diagnose_incident` builds `detail` strings like `config: notify not in the pipeline's stages`
and `error: no_channel: ... (retryable=False)`. Keep them for the admin, and add the raw parts
so the console can describe them.

**Files:**
- Modify: `apps/alerts/diagnosis.py` (`_diagnose_stage`, `_classify_from_execution`)
- Test: `apps/alerts/_tests/test_diagnosis.py`

**Step 1: Failing test.** Each entry also has `execution_status` (the `StageStatus` value or
`None`), `error_message` (str) and `retryable` (bool). A config skip has
`execution_status="skipped"` and `error_message="Skipped: not in this route's stages"`.

**Step 2 to 4:** add the keys at both sites. Admin output stays identical.

**Step 5: Commit** `feat(alerts): diagnosis carries the raw stage outcome`.

---

### Task 8: Alert history in words

**Files:**
- Modify: `apps/alerts/describing.py` (`describe_history`)
- Modify: `apps/alerts/timeline.py` (add `old_status`, `new_status`, `severity_from` to each history entry)
- Test: `apps/alerts/_tests/test_describing.py`

**Step 1: Failing tests** for `describe_history(event, *, alert_headline, old_status,
new_status, severity_from, severity_to) -> Description`:

| event | headline |
|---|---|
| `created` | `{alert} started firing` |
| `resolved` | `{alert} cleared` |
| `refired` | `{alert} fired again` |
| `reevaluated` with severity change | `{alert} moved from warning to critical` |
| `updated` with no change | `{alert} updated` |
| anything else | `{alert} changed` |

Tone from `history_tone` (move it from `apps/console/queries/incident.py` into this module).
No headline contains `→` or `—`.

**Step 2 to 4.** The console passes `describe_alert(alert).headline` as `alert_headline`, so
checker alerts read "High CPU usage" rather than the stored name.

**Step 5: Commit** `feat(alerts): describe alert history events`.

---

### Task 9: Readiness as descriptions

**Files:**
- Modify: `config/dashboard.py` (`build_readiness` adds structured keys, new `describe_readiness`)
- Test: `config/_tests/test_dashboard.py`

**Step 1: Failing tests.** `describe_readiness()` returns `tuple[Description, ...]`, one per
entry that is `warn` or `error`, in `build_readiness` order. Cases:

| state | headline | action intent |
|---|---|---|
| no active channel, lanes deliver | `No active channel, 2 routes can't deliver` | `edit_channel` |
| lane gap `no_channel` on `catch-all` | `Route catch-all can't deliver: it has no active channel` | `edit_route` |
| no active provider | `No AI provider is active, analysis is skipped` | `edit_provider` |
| preflight `error` | `Preflight found 2 errors` | `view_preflight` |
| 3 stuck runs | `3 runs are stuck` | `view_inbox` with `params={"status": "processing"}` |
| 4 pending | `4 runs are waiting to be processed` | `view_inbox` with `params={"status": "pending"}` |
| peers silent | `2 of 5 nodes stopped reporting` | `view_nodes` |

The gap sentence comes from `apps.orchestration.describing.REASONS`, not a second table.

`build_readiness` entries gain `gaps` (list of `(lane name, code)`), `counts` (dict) and keep
`label`, `detail` and `url` unchanged for the admin.

**Step 2 to 4.**

**Step 5: Commit** `feat(config): describe readiness with fixes`.

---

### Task 10: Intent table

**Files:**
- Create: `apps/console/intents.py`
- Create: `apps/console/templatetags/__init__.py`, `apps/console/templatetags/console.py`
- Test: `apps/console/_tests/test_intents.py`

**Step 1: Failing tests.**

```python
import re
from pathlib import Path

import pytest

from apps.console.intents import INTENTS, resolve_action
from config.describing import Action

ROOT = Path(__file__).resolve().parents[3]


def emitted_intents() -> set[str]:
    found = set()
    for path in [*ROOT.glob("apps/**/*.py"), *ROOT.glob("config/**/*.py")]:
        if "_tests" in path.parts:
            continue
        found |= set(re.findall(r'intent="([a-z_]+)"', path.read_text()))
    return found


def test_every_emitted_intent_is_known():
    assert emitted_intents() <= set(INTENTS)


def test_unknown_intent_is_text(rf, staff_user):
    resolved = resolve_action(Action("Do it", intent="nope"), staff_user)
    assert (resolved.text, resolved.url, resolved.method) == ("Do it", "", "")


def test_missing_permission_is_text(staff_user):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), staff_user)
    assert resolved.url == ""


def test_permitted_link(superuser):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), superuser)
    assert resolved.method == "get"
    assert resolved.url == "/admin/notify/notificationchannel/"


def test_post_intent(superuser, incident):
    resolved = resolve_action(
        Action("Acknowledge", intent="acknowledge", params={"pk": incident.pk}), superuser
    )
    assert resolved.method == "post"
    assert resolved.url == f"/console/incidents/{incident.pk}/acknowledge/"


def test_open_url_needs_no_permission(staff_user):
    resolved = resolve_action(
        Action("Runbook", intent="open_url", params={"url": "https://x"}), staff_user
    )
    assert resolved.url == "https://x"
```

Use the fixtures the console view tests already define. Move them into
`apps/console/_tests/conftest.py` if they live in a test module.

**Step 2: Run.** Expected: ImportError.

**Step 3: Implement.**

```python
"""What each action intent does in the console: a link, a POST button, or text.

Config intents point at the admin until each domain slice gives them a console page.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from config.describing import Action


@dataclass(frozen=True)
class Intent:
    url: Callable[[dict[str, Any]], str]
    method: str
    permission: str


@dataclass(frozen=True)
class ResolvedAction:
    text: str
    url: str = ""
    method: str = ""


def _route(name: str, *keys: str, query: str = "") -> Callable[[dict[str, Any]], str]:
    def build(params: dict[str, Any]) -> str:
        url = reverse(name, args=[params[key] for key in keys])
        value = params.get(query) if query else None
        return f"{url}?{query}__exact={value}" if value else url

    return build


def _incident(action: str) -> Intent:
    return Intent(
        lambda p: reverse("console:incident-action", args=[p["pk"], action]),
        "post",
        "alerts.change_incident",
    )


INTENTS: dict[str, Intent] = {
    "open_url": Intent(lambda p: p["url"], "get", ""),
    "acknowledge": _incident("acknowledge"),
    "resolve": _incident("resolve"),
    "close": _incident("close"),
    "rerun_check": Intent(
        lambda p: reverse("console:incident-rerun", args=[p["pk"]]), "post", "checkers.add_checkrun"
    ),
    "edit_channel": Intent(
        _route("admin:notify_notificationchannel_changelist"), "get",
        "notify.change_notificationchannel",
    ),
    "edit_route": Intent(
        _route("admin:orchestration_pipelinedefinition_changelist"), "get",
        "orchestration.change_pipelinedefinition",
    ),
    "edit_provider": Intent(
        _route("admin:intelligence_intelligenceprovider_changelist"), "get",
        "intelligence.change_intelligenceprovider",
    ),
    "view_preflight": Intent(
        _route("admin:checkers_preflightrun_changelist"), "get", "checkers.view_preflightrun"
    ),
    "view_inbox": Intent(
        _route("admin:orchestration_inboxitem_changelist", query="status"), "get",
        "orchestration.view_inboxitem",
    ),
    "view_nodes": Intent(_route("admin:alerts_node_changelist"), "get", "alerts.view_node"),
    "view_node": Intent(_route("admin:alerts_node_change", "pk"), "get", "alerts.view_node"),
    "view_run": Intent(
        _route("admin:orchestration_pipelinerun_change", "pk"), "get",
        "orchestration.view_pipelinerun",
    ),
    "view_alert": Intent(_route("admin:alerts_alert_change", "pk"), "get", "alerts.view_alert"),
}


def resolve_action(action: Action, user) -> ResolvedAction:
    intent = INTENTS.get(action.intent)
    if intent is None or (intent.permission and not user.has_perm(intent.permission)):
        return ResolvedAction(action.text)
    try:
        url = intent.url(action.params)
    except (KeyError, NoReverseMatch):
        return ResolvedAction(action.text)
    return ResolvedAction(action.text, url, intent.method)
```

Check the proxy model's permission codename (`view_inboxitem`) against `auth_permission`
before relying on it. Black reflows the long entries.

Template tag in `templatetags/console.py`:

```python
from django import template

from apps.console.intents import resolve_action

register = template.Library()


@register.simple_tag(takes_context=True)
def resolved(context, action):
    return resolve_action(action, context["request"].user) if action else None
```

**Step 4: Run.** Expected: PASS.

**Step 5: Commit** `feat(console): resolve action intents to links and buttons`.

---

### Task 11: The description partial, Details and relative time

**Files:**
- Create: `templates/console/partials/_description.html`, `templates/console/partials/_details.html`, `templates/console/partials/_time.html`
- Modify: `static/console/console.css` (`c-description`, `c-details`, `c-facts`, `c-copy`)
- Modify: `static/console/console.js` (copy button)
- Test: `apps/console/_tests/test_templates.py`

`_description.html` takes `d` (a `Description`) and `id` (stable, for polling focus):

```django
{% load console %}{% resolved d.action as act %}
<div class="c-description c-tone-{{ d.tone }}">
  <p class="c-description__headline">{% include "console/partials/_tone_icon.html" with tone=d.tone %}<span>{{ d.headline }}</span></p>
  {% if d.detail %}<p class="c-description__detail">{{ d.detail }}</p>{% endif %}
  {% if act %}{% include "console/partials/_action.html" with act=act id=id|add:"-action" %}{% endif %}
  {% if d.facts %}{% include "console/partials/_details.html" with facts=d.facts id=id|add:"-details" %}{% endif %}
</div>
```

`_action.html`: a `<form method="post">` with `{% csrf_token %}` and a `c-button` when
`act.method == "post"`, an `<a class="c-button c-button--sm">` when `"get"` (external
`open_url` adds `rel="noopener noreferrer"` and `target="_blank"`), else `<p class="c-description__action">{{ act.text }}</p>`.

`_details.html` is a `<details id="{{ id }}" class="c-details" data-details>` with a
`<summary id="{{ id }}-summary">Details</summary>`, a `<dl class="c-facts">` of facts, a
`<button type="button" class="c-copy" data-copy="{{ fact.value }}">` on `copy` facts, and an
optional nested raw JSON `<details>` when the caller passes `raw`. Every Details block carries
`data-details`, which is what the guard test strips.

`_time.html` takes `when`:
`<time datetime="{{ when|date:'c' }}" title="{{ when|date:'Y-m-d H:i:s T' }}">{{ when|timesince }} ago</time>`.

`console.js`: one delegated `click` listener on `[data-copy]` that calls
`navigator.clipboard.writeText` and sets the button's text to "Copied" for two seconds.

CSS: reuse `c-box`, `c-label`, `c-button`. New rules go in the components section and use tokens
only. `test_css.py` must still pass.

**Tests:** extend `test_templates.py` so the octicon and inline-script scans cover the new
partials (they glob the directory, so confirm the new files are picked up). Add a render test
for `_description.html` with each `ResolvedAction` method.

**Commit** `feat(console): render descriptions, actions and Details`.

---

### Task 12: Rerun a local check

**Files:**
- Modify: `apps/alerts/check_integration.py` (add `record_and_enqueue`, `rerun_incident_checks`)
- Modify: `apps/checkers/management/commands/check_health.py` (`_record_alerts` uses `record_and_enqueue`)
- Create: `apps/console/views/incident_rerun.py`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`
- Test: `apps/alerts/_tests/test_check_integration.py`, `apps/console/_tests/views/test_incident_rerun.py`

**Step 1: Failing tests.**

- `record_and_enqueue(results, *, trace_id, sync, no_notify=False)` writes alerts and enqueues
  in one `transaction.atomic()` block. `check_health`'s existing tests keep passing unchanged.
- `rerun_incident_checks(incident)` runs the incident's checker labels that exist in the local
  registry, through `CheckAlertBridge.run_checks_and_alert(checker_names=...)`, and enqueues with
  `sync=False`. It returns the `CheckAlertResult`. It raises `ValueError` when the incident's
  `instance` isn't `local_instance_id()` or names no local checker.
- `can_rerun(incident) -> bool` answers the same question without running anything.
- The view: POST `incidents/<pk>/rerun/` needs `checkers.add_checkrun` (403 otherwise), renders
  the incident partial with a `Notice` ("Checks ran again. The incident updates when the run
  finishes.") for htmx, or a 303 with a message for a plain POST, like `IncidentActionView`.
  A refused rerun is a warning notice, not a 500. Mock `CheckAlertBridge.run_checks_and_alert`
  in view tests. Never drain the inbox in a test.

**Step 2 to 4.** Subclass `IncidentPageView` so the page and response can't drift.

**Step 5: Commit** `feat(console): rerun an incident's checks on this machine`.

---

### Task 13: Incident page on describers

**Files:**
- Modify: `apps/console/queries/incident.py`
- Modify: `templates/console/partials/_incident.html`, `_stage_body.html`, `_entry_lead.html`
- Modify: `apps/console/views/incident.py` (pass `can_rerun`)
- Test: `apps/console/_tests/queries/test_incident.py`, `apps/console/_tests/views/test_incident.py`

**Read model changes:**

- `IncidentDetail` drops `title`, `description` and `admin_url`. It gains
  `what: Description` (from `describe_incident`), `do: tuple[Action, ...]` (the transitions
  from `available_incident_actions` as `acknowledge`/`resolve`/`close` intents, plus
  `rerun_check` when `can_rerun`), and keeps `summary`.
- `StageSummary` becomes `stage`, `label`, `outcome: Description`. The outcome comes from
  `describe_stage_outcome` on the new diagnosis keys. `never_ran` reads "Hasn't run yet" with
  tone `muted`. `execution_pk` becomes a `view_run` action in the outcome's facts.
- `TimelineEntry` drops `title`, `detail` and `url`, gains `what: Description`:
  history entries from `describe_history`, pipeline entries from `describe_run`, stage entries
  from `describe_stage_outcome`. Lifecycle entries keep their fixed wording as descriptions.
- `CheckBody` becomes `problems: tuple[Description, ...]` (worst first), `ok_count`,
  `skipped_count`, `raw`. Each line goes through `CHECKER_REGISTRY[name]().describe(status,
  metrics, message)`. An unknown checker falls back to the message with a "Described by the
  node" fact, as `describe_alert` does. Errors become `Error` facts.
- `AnalyzeBody` becomes `outcome: Description` plus `recommendations` and `actions` only when
  the analysis didn't fail. `confidence` and `fallback_used` go.
- `NotifyBody` becomes `deliveries: tuple[Description, ...]` from `describe_notify`.
- `RawBody` stays and renders only inside Details.
- No `reverse("admin:...")` call remains in this module. Grep for it.

**Template changes:**

- The page head shows `incident.what.headline`, the status state, host and relative start time.
  "Edit raw" goes.
- A "What's wrong" box first in the main column: `_description.html` with `incident.what`.
  Under it a "Do this" row: the description's own action, then each `do` action as a button
  (`close` keeps `c-button--danger`, the first keeps `c-button--primary`). The resolve note
  `<details>` stays, under the buttons. The sidebar Actions block goes.
- Timeline items render `entry.what` through `_description.html`. A stage body sits inside
  the entry's `<details>`, and its raw JSON moves into that entry's Details. "Open in admin"
  goes.
- Check bodies list problems, then one muted line: "12 checks passed, 2 skipped".
- Every time goes through `_time.html`.
- Sidebar stages render `stage.outcome.headline` with its tone icon.

**Tests:** update every assertion on the old fields. Add: a check body with OK, skipped and
critical lines orders the critical first and counts the rest; a failed analysis renders no
"confidence"; a `no_channel` notify failure renders the `edit_route` action as a link for a
superuser and as text for a staff user without the permission; a non-local incident shows the
rerun as text; the incident page has no `href="/admin/` for a staff user without admin model
permissions.

**Commit** `feat(console): incident page answers what's wrong and what to do`.

---

### Task 14: Overview and incident list

**Files:**
- Modify: `apps/console/queries/overview.py`, `apps/console/queries/incidents.py`
- Modify: `templates/console/partials/_overview.html`, `_incident_row.html`
- Test: matching query and view tests

- `Overview.attention` becomes `readiness: tuple[Description, ...]` from `describe_readiness()`.
  The Readiness box renders one `_description.html` per row with id `readiness-<n>`, or the one
  line "System ready." when empty.
- `NodeTile.url` becomes `action: Action` with `view_node`. The tile is a link only when the
  resolved action has a URL, otherwise a plain `c-box`.
- `IncidentRow` and `IncidentListRow` gain `detail` (the description's detail, one line,
  truncated in CSS) shown under the title. `title` stays as the headline.
- Every time uses `_time.html`. The node tile keeps "seen ... ago" wording.

**Commit** `feat(console): overview and list on describers`.

---

### Task 15: Reads use the admin's view permissions

**Files:**
- Modify: `apps/console/views/base.py` (`ConsoleView.read_permission`)
- Modify: the three page views
- Test: `apps/console/_tests/views/test_*.py`

`ConsoleView` gains `read_permission: str = ""`. `_refusal` returns
`HttpResponseForbidden("Permission to view incidents required.")` when it's set and the user
lacks it. Overview, the list and the incident page set `alerts.view_incident`. Action views
keep their own checks on top.

Tests: staff without `alerts.view_incident` gets 403 on each page; with it, 200. Update the
`staff_user` fixture to hold `alerts.view_incident`, and add `bare_staff_user` without it.

**Commit** `feat(console)!: reads require the admin's view permission`.

---

### Task 16: Guard test

**Files:**
- Create: `apps/console/_tests/test_guard.py`
- Create: `apps/console/_tests/fixtures.py` (`build_world()`)

`build_world()` creates, through the models directly, one incident per case:

- a checker incident on this machine with a check body holding critical, warning, OK, skipped
  and unknown lines, and an unknown checker name;
- a webhook incident with a runbook annotation;
- runs failed with each of `ROUTING_GAPS`, a retryable failure, a skipped stage, a stalled stage;
- an analysis failed with each of `FAILURE_KINDS`, and a successful one;
- deliveries failed with each of `FAILURE_KINDS`, and a successful one;
- alert history rows for `created`, `resolved`, `refired`, `reevaluated` and `updated`;
- readiness faults: no channel with a delivering lane, a `no_driver` gap, no provider, a failed
  preflight, a stuck run, a silent peer.

Every `run_id`, `trace_id` and fingerprint is a real UUID, so a leak is caught.

```python
import re
from html.parser import HTMLParser

import pytest

from apps.console._tests.fixtures import build_world
from apps.orchestration.describing import REASONS
from config.failures import FAILURE_KINDS

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")
CODES = set(REASONS) | set(FAILURE_KINDS)
VOID = {"br", "img", "input", "meta", "link", "hr", "use"}


class VisibleText(HTMLParser):
    """Text a reader sees without opening Details. Skips script, style and data-details."""

    def __init__(self):
        super().__init__()
        self.hidden: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        if self.hidden:
            self.hidden.append(tag)
        elif tag in ("script", "style") or "data-details" in dict(attrs):
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def visible(html: str) -> str:
    parser = VisibleText()
    parser.feed(html)
    return " ".join(parser.chunks)


def test_visible_skips_details():
    html = '<p>a<details data-details><details><summary>x</summary>y</details>z</details>b</p>'
    assert visible(html).split() == ["a", "b"]
```

```python
@pytest.mark.django_db
@pytest.mark.parametrize("partial", [False, True])
def test_no_page_leaks_identifiers(client, superuser, partial):
    world = build_world()
    client.force_login(superuser)
    headers = {"HX-Request": "true"} if partial else {}
    urls = ["/console/", "/console/incidents/?status=all"] + [
        f"/console/incidents/{pk}/?all=1" for pk in world.incident_pks
    ]
    for url in urls:
        text = visible(client.get(url, headers=headers).content.decode())
        assert not UUID.search(text), url
        assert not SNAKE.search(text), (url, SNAKE.findall(text))
        assert "{" not in text, url
        assert not CODES & set(re.findall(r"[a-z_]+", text)), url
```

`unknown` and `timeout` are ordinary words. If they collide in rendered text, narrow `CODES`
to the codes that contain an underscore and say so in the test. Don't loosen `SNAKE`.

Run it. Each failure names a leak: fix the describer or move the text into Details. Don't
add an allowlist.

**Commit** `test(console): no identifiers outside Details`.

---

### Task 17: Docs

- `apps/console/AGENTS.md`: replace "Reads need active staff, nothing more" with the
  `read_permission` rule. Add rules for `intents.py` (config intents point at the admin until
  their slice, no template or query builds an admin URL), `_description.html`, `data-details`
  and the guard test. Drop "Open in admin" and "Edit raw" mentions. Add the rerun view under the
  incident actions rule, with "never drain in a test".
- `apps/orchestration/AGENTS.md`: the reason registry, `ROUTING_GAPS`, "every routing gap has
  a reason".
- `apps/intelligence/AGENTS.md`: providers raise on failure; the executor classifies it;
  confidence is `None` until a provider reports one.
- `apps/notify/AGENTS.md`: deliveries carry `failure`; `describe_delivery`.
- `AGENTS.md` convention 14: add the orchestration, intelligence and notify describers and
  `config/failures.py`.
- `docs/Architecture.md`: the console section, if it describes reads or admin links.

**Commit** `docs: console renders describers; stage reasons live with their apps`.

---

## Verification

```bash
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run black . --check && uv run ruff check .
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py check
```

By hand: `uv run python manage.py runserver`, log in as staff, open `/console/`, an incident
with a failed analysis, and one with a `no_channel` failure. Check copy buttons, Details, the
rerun button on a local incident and its text form on a peer's, in light and dark.

## Acceptance

- Every incident page leads with what's wrong and what to do.
- A failed analysis shows its cause and no confidence.
- No page shows a UUID, `snake_case` token, reason code or `{` outside Details. The guard test
  proves it against every outcome and reason code.
- No console template or query builds an admin URL. `intents.py` is the one place that does.
