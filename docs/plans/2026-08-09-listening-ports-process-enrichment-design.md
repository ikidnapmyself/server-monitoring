---
title: "Enrich listening ports with owning process"
parent: Plans
---

# Enrich listening ports with owning process (name + user)

## Motivation

`ListeningPortsChecker` reports `IP:PORT` for each LISTENing socket, but an
operator seeing an unexpected port (e.g. `9999(0.0.0.0)`) cannot tell **what**
is bound there. `psutil.net_connections` already yields the socket's `pid`; the
checker captures it in `ListeningPort.pid` but never resolves it. This slice
resolves each PID to its **process name** and **owning username**.

## Scope

- **Process name + username only.** Command line is intentionally excluded: a
  process cmdline routinely carries secrets (`--password=…`, tokens), and the
  project rule is "never store secrets." Name + user answer "what app, run by
  whom" with negligible leak risk.
- **Node-side only.** A PID is meaningful only on the host that owns the socket.
  The hub-side allowlist re-eval (`_score_allowlist`) reads only `port` /
  `exposed`, so the new fields ride along in metrics and change nothing
  downstream.

## Data model

`ListeningPort` gains two optional fields (defaults keep existing constructors
and `flagged_ports` tests working unchanged):

```python
process: str | None = None    # psutil name(), e.g. "sshd"
username: str | None = None   # owner, e.g. "root"
```

## Resolution

After `collect_listening()`, resolve each **unique** non-None PID once (many
sockets share a PID — IPv4+IPv6, pre-fork workers) and stamp the result onto
every socket with that PID:

```python
psutil.Process(pid).as_dict(attrs=["name", "username"], ad_value=None)
```

Fail-soft per PID:

- `pid is None` (socket psutil could not attribute) → fields stay `None`.
- `NoSuchProcess` (died between the two reads) → `None`.
- `AccessDenied` / `ZombieProcess` per field → `None` (via `ad_value`), no raise.

The checker's existing non-Linux skip and `AccessDenied` on
`net_connections` paths are unchanged.

## Metrics

Each entry in `metrics["listening"]` gains `process` and `username` alongside the
existing `port` / `address` / `family` / `pid` / `exposed`.

## Message

Stays a short one-liner, name only (username lives in metrics, not the message):

```
2 unexpected listening port(s): 9999(0.0.0.0) [python3], 22(0.0.0.0) [sshd]
```

A port whose process could not be resolved renders `[?]`.

## Tests

- PID resolution: happy path (name+user), `pid=None`, `NoSuchProcess`,
  `AccessDenied` field → `None`, and shared-PID resolved once (cache).
- `check()` populates `process` / `username` on flagged + inventoried ports.
- Metrics include the new fields; message includes the process name and `[?]`
  fallback.
- Existing `flagged_ports` / collection tests still pass (dataclass defaults).
- 100% branch coverage on changed lines.

## Out of scope

cmdline capture (secret risk), a config toggle for enrichment (YAGNI), and any
hub-side use of process identity.
