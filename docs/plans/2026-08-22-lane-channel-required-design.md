---
title: "A Lane Delivers to Its Own Channel, or Not at All"
parent: Plans
---

# A lane delivers to its own channel, or not at all

**Status:** design approved 2026-08-22. Closes the last implicit fallback in the delivery path,
the one named as next in `docs/plans/2026-08-19-incident-fanout-design.md`.

---

## 1. The problem

`NotifySelector.resolve` (`apps/notify/services.py:66-78`) picks **the first active channel ordered
by name** whenever the caller names no provider. For the pipeline that is a silent misroute: a lane
whose `channel` FK is unset or inactive routes nowhere (`routed_channel()` returns `None`), the
executor passes no provider, and the message goes wherever the alphabet points. A critical database
alert lands in `#aaa-general` because of its name.

This is the same defect class as the routing fallback deleted in the routing simplification: a
default that lives in code, is invisible in the data, and cannot be read or edited by an operator.

**It is not an edge case — it is the current default path.** The seeded lanes carry no channel FK
(migration `0012`), and `_bind_catchall_pipeline` — the `setup_cluster` step its docstring refers
to — no longer exists. So unless an operator has set a lane's channel by hand, *every* notification
this hub sends is resolved by the fallback. Deleting it without replacing it would silence the hub.

## 2. The shape

### 2.1 A fresh routing table that matches how the hub is actually configured

**A channel is optional; a lane that lists `notify` is not.** The `stages` column *is* the
statement of intent — `hub-self-check` already ships with `stages: []` to mean "record it, tell
nobody", which is a supported way to run this hub. An operator who reads the admin daily and runs
no Slack is not misconfigured; they simply have no lane that claims to deliver.

So the seed must not manufacture an intent the operator never expressed. It reads how many active
channels exist and seeds accordingly:

| Active channels | Seeded stages | Channel | Readiness |
|---|---|---|---|
| **0** | `notify` omitted — e.g. `catch-all` → `check`, `analyze` | — | `info`: recording only |
| **1** | as designed, `notify` included | bound | `ok` |
| **2+** | `notify` included | left unset | `error`: pick one |

The lanes themselves:

| Lane | Priority | Stages (with a channel) | Stages (without) |
|---|---|---|---|
| `resolved-all-clear` | 40 | `notify` | *(inactive — it exists only to notify)* |
| `cluster-nodes` | 50 | `analyze`, `notify` | `analyze` |
| `hub-self-check` | 50 | *(none)* | *(none)* |
| `catch-all` | 1000 | `check`, `analyze`, `notify` | `check`, `analyze` |

Binding happens **only when exactly one active channel exists**. With two or more there is no
non-arbitrary answer, and picking one by name is precisely the bug being removed. `get_or_create`
on `name` means an operator's existing row is never overwritten, and binding only fills a `channel`
that is `NULL`.

`resolved-all-clear` is seeded `is_active=False` on a channel-less hub: its whole purpose is to
notify an all-clear without analysing, so with nothing to notify it would only shadow the lanes
below it. Everything else keeps routing.

### 2.2 The pipeline stops guessing

A lane with no active channel now fails NOTIFY as a **non-retryable `no_channel`**
`StageExecutionError`, the same shape as `no_route`. Given §2.1 this fires only when a row
*claims* it will deliver and cannot — never merely because the hub has no channel. Nothing about a retry can conjure a channel;
the run is undeliverable until an operator configures one, and a retryable failure would spin.

The fallback itself is not deleted from `NotifySelector` — it is *scoped*. `resolve()` gains
`allow_default_channel: bool = False`. The two interactive callers pass `True`:

- `manage.py test_notify` — "send a test message" with no argument means "use my channel".
- the notify webhook view — the caller is an operator asking for a send.

There, defaulting to the single configured channel is the intent, not a guess about routing. The
pipeline passes nothing and therefore gets the strict behaviour.

### 2.3 A hub that cannot deliver says so

Two read-only surfaces, so "the hub went quiet" is diagnosable before an incident rather than
after:

- **Readiness panel** (`config/dashboard.py:build_readiness`) — a new entry counting lanes that
  route to NOTIFY but have no active channel. `error` when any exist; `info` ("recording only")
  when no lane delivers and no channel is configured, which is a valid way to run; and a nudge when
  a channel exists but no lane points at it, which is a hub one edit away from delivering.
- **Preflight** (`check_pipeline_state`) — the same finding as a `warn` with a hint naming the
  admin page.

## 3. Behaviour changes

1. A pipeline run whose lane has no active channel **fails** instead of delivering somewhere else.
   This is the point of the change, and the reason §2.1 seeds intent to match the hub first: a
   channel-less hub records rather than failing, because it never claimed to deliver.
2. Nothing changes for `test_notify` or the notify webhook.
3. Nothing changes for a lane that already names an active channel.

## 4. Testing

- The seed with **zero** channels omits `notify` from every lane, deactivates
  `resolved-all-clear`, and produces a hub where nothing fails.
- The seed with **one** channel includes `notify` and binds it; with **two or more** it includes
  `notify` and binds nothing.
- The seed never overwrites a channel or a `stages` list an operator already set.
- `hub-self-check` (no stages) is not bound and not reported.
- `no_driver`: a lane naming a channel whose driver is not registered fails non-retryably rather
  than retrying three times.
- A downstream run whose lane has no active channel fails `no_channel`, non-retryably, and delivers
  nothing.
- An inactive channel on a lane behaves as no channel (`routed_channel()` is the one rule).
- `test_notify` and the webhook still default to the single channel.
- Readiness and preflight report the undeliverable lane.

## 5. Risk

The failure mode this introduces is loud (a FAILED run naming `no_channel`) and the one it removes
is silent (delivery to the wrong place). That trade is the whole point. The residual risk is an
operator with several channels who never binds one: their lanes fail rather than misdeliver, and
both surfaces in §2.3 name the fix.

## 6. A third gap, same construct: `no_driver`

A lane can name a channel whose `driver` is not in `DRIVER_REGISTRY` — a driver removed, a typo in
the field. Today `NotifyExecutor` appends "Unknown notify driver/provider" to the result, which the
orchestrator turns into a **retryable** failure: three attempts, then a FAILED run an operator can
"Mark for Retry" to spin again. No retry can invent a driver.

That is the third and last way the routing table fails to say where work goes:

| Code | Missing |
|---|---|
| `no_route` | no lane matched the alert |
| `no_channel` | the lane names no active channel |
| `no_driver` | the lane names a channel whose driver does not exist |

All three use `routing_gap()`. The set is closed — it is the routing table's own structure, not an
open-ended list of components.

**Deliberately NOT gaps:** a missing AI provider (intelligence failure is a *designed* downgrade —
`fallback_used`, recorded per-run, notification still sent) and an unconfigured optional
integration such as PagerDuty or Grafana. The rule that keeps preflight readable: **diagnose
dangling references, not absences.** Report a row that points at something missing; never report
that an optional thing is simply not there.

## 7. Out of scope

- Per-lane multi-channel fan-out. Delivery has never fanned out; `channel` is a single FK.
- Reviving `setup_cluster`. The migration covers a fresh install; the admin covers the rest.
