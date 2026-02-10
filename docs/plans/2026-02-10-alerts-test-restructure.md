# Alerts App Test Restructure

## Goal

Migrate `apps/alerts/tests.py` (monolithic, ~1065 lines, 13 test classes) into the `_tests/` package layout prescribed by `apps/alerts/agents.md`.

## Mapping

| Source class | Target file |
|---|---|
| `AlertManagerDriverTests` | `_tests/drivers/test_alertmanager.py` |
| `GrafanaDriverTests` | `_tests/drivers/test_grafana.py` |
| `GenericDriverTests` | `_tests/drivers/test_generic.py` |
| `PagerDutyDriverTests` | `_tests/drivers/test_pagerduty.py` |
| `NewRelicDriverTests` | `_tests/drivers/test_newrelic.py` |
| `DatadogDriverTests` | `_tests/drivers/test_datadog.py` |
| `ZabbixDriverTests` | `_tests/drivers/test_zabbix.py` |
| `DriverDetectionTests` | `_tests/drivers/test_detection.py` |
| `WebhookViewTests` + `WebhookViewPartialResponseTests` | `_tests/views/test_webhook.py` |
| `AlertModelTests` + `IncidentModelTests` | `_tests/test_models.py` |
| `AlertOrchestratorTests` + `AlertOrchestratorEdgeCaseTests` + `IncidentManagerTests` | `_tests/test_services.py` |
| `CheckAlertBridgeTests` | `_tests/test_check_integration.py` |
| `ServiceOrchestratorTasksTests` | `_tests/test_tasks.py` |

## Rules

- No test logic changes — only move code and adjust imports per file.
- Each file gets only the imports it needs.
- Delete `tests.py` after all tests pass from `_tests/`.