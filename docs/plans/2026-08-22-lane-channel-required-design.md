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

### 2.1 A fresh, complete routing table, seeded

A new migration `get_or_create`s the four default lanes and **binds an active channel** to each
lane that actually delivers:

| Lane | Priority | Stages | Channel |
|---|---|---|---|
| `resolved-all-clear` | 40 | `notify` | bound |
| `cluster-nodes` | 50 | `analyze`, `notify` | bound |
| `hub-self-check` | 50 | *(none)* | — |
| `catch-all` | 1000 | `check`, `analyze`, `notify` | bound |

It binds **only when exactly one active channel exists**. With two or more there is no
non-arbitrary answer, and picking one by name is precisely the bug being removed; those lanes stay
unbound and are reported (§2.3) until an operator chooses. `hub-self-check` lists no stages, never
notifies, and needs no channel.

`get_or_create` on `name` means an operator's existing row is never overwritten, and binding only
fills a `channel` that is `NULL` — a lane an operator has already pointed somewhere is left alone.

### 2.2 The pipeline stops guessing

A lane with no active channel now fails NOTIFY as a **non-retryable `no_channel`**
`StageExecutionError`, the same shape as `no_route`. Nothing about a retry can conjure a channel;
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
  route to NOTIFY but have no active channel. `error` when any exist.
- **Preflight** (`check_pipeline_state`) — the same finding as a `warn` with a hint naming the
  admin page.

## 3. Behaviour changes

1. A pipeline run whose lane has no active channel **fails** instead of delivering somewhere else.
   This is the point of the change, and the reason §2.1 seeds the binding first.
2. Nothing changes for `test_notify` or the notify webhook.
3. Nothing changes for a lane that already names an active channel.

## 4. Testing

- The seed binds when exactly one active channel exists, binds nothing when zero or several, and
  never overwrites a channel an operator already set.
- `hub-self-check` (no stages) is not bound and not reported.
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

## 6. Out of scope

- Per-lane multi-channel fan-out. Delivery has never fanned out; `channel` is a single FK.
- Reviving `setup_cluster`. The migration covers a fresh install; the admin covers the rest.
