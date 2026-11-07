---
title: "Drop the Payload Provider Override"
parent: Plans
---

# The analyze stage stops reading a provider from the run payload

`AnalyzeExecutor` reads `provider` and `provider_config` from the run payload and passes
`provider_config` straight to the provider constructor. No producer writes those keys any
more: every run goes through `enqueue_for`, whose payload is `{"downstream_incident_id": N}`.
The only rows that could still carry them are legacy `{"driver", "payload"}` runs draining
from before #223.

## Design

- `apps/orchestration/executors.py`: the analyze stage always calls `get_active_provider()`
  with no kwargs and calls `provider.run` without `provider_config`. The active
  `IntelligenceProvider` row, configured in the console, is the only source.
- A legacy row that still carries an override runs on the active provider instead.
- `/intelligence/recommendations/` keeps its `provider` and `config` body fields. It is a
  separate, live API and is out of scope.
- The notify payload keys (`notify_driver`, `notify_config`, `notify_channel`) are out of scope.
- `apps/orchestration/AGENTS.md`, `apps/intelligence/AGENTS.md` and `docs/Security.md` stop
  saying `/orchestration/*` forwards `provider_config` to a provider.

## Tests

- A payload carrying `provider` and `provider_config` still calls `get_active_provider()` with
  no arguments, never `get_provider`, and `provider.run` gets no `provider_config`.
- The explicit-provider tests go.
