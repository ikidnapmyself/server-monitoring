---
title: "Views Packages for Alerts, Notify and Orchestration"
parent: Plans
---

# The last three `views.py` modules become packages

AGENTS.md lists `apps/alerts/views.py`, `apps/notify/views.py` and
`apps/orchestration/views.py` as pending migration to the `views/` package form. This moves
them. No behaviour changes.

## Design

- `apps/alerts/views/webhook.py` holds `AlertWebhookView`. Its tests already live in
  `apps/alerts/_tests/views/test_webhook.py`.
- `apps/notify/views/`: `send.py` (`NotifyView`), `batch.py` (`NotifyBatchView`),
  `drivers.py` (`DriversView`).
- `apps/orchestration/views/`: `base.py` (`JSONResponseMixin`), `pipeline.py`
  (`PipelineView`), `status.py` (`PipelineStatusView`), `list.py` (`PipelineListView`),
  `resume.py` (`PipelineResumeView`).
- Each `views/__init__.py` re-exports its view classes with `__all__`, as
  `apps/intelligence/views/__init__.py` does, so `urls.py` imports stay as they are.
- `apps/notify/views.py` carries its own copy of `DRIVER_REGISTRY`, which duplicates
  `apps.notify.drivers.DRIVER_REGISTRY`. The copy goes. The views and every outside importer
  (`apps/notify/services.py`, `apps/orchestration/models.py`,
  `apps/orchestration/executors.py`, `config/management/commands/setup_cluster.py`) import
  `apps.notify.drivers.DRIVER_REGISTRY`.
- Tests move to mirror the source: `apps/notify/_tests/views/test_<module>.py` and
  `apps/orchestration/_tests/views/test_<module>.py`. `patch()` targets follow the code.
- AGENTS.md, `.github/copilot-instructions.md`, `apps/alerts/AGENTS.md`,
  `docs/Architecture.md` and the `apps/orchestration/intake.py` docstring point at the new
  paths, and AGENTS.md drops the "pending migration" sentence.

## Tests

The moved tests pass unchanged apart from imports and patch targets. The full suite and
100% branch coverage on the moved modules hold.
