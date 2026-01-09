# Alerts

This Django app provides alert ingestion and incident orchestration.

It exposes a simple webhook endpoint that accepts JSON payloads from multiple alert sources (Alertmanager, Grafana, and a generic/fallback format), normalizes them into a common schema, and stores them as `Alert` + `Incident` records.

> **Note:** For development setup (formatting, linting, testing), see the main [README](../../README.md#development).

## What's included

### Webhook endpoint

The app exposes two endpoints:

- `POST /alerts/webhook/` — auto-detect driver
- `POST /alerts/webhook/<driver>/` — force a specific driver

A `GET` request to the same URLs acts as a small health check.

### Drivers (payload parsers)

Drivers live in `apps/alerts/drivers/` and are responsible for:

- validating a payload (`validate()`)
- parsing it into a normalized `ParsedPayload` (`parse()`)

Built-in drivers:

- `alertmanager` — Prometheus Alertmanager webhook format
- `grafana` — Grafana Unified Alerting webhook format
- `pagerduty` — PagerDuty V2/V3 webhook format
- `datadog` — Datadog webhook format
- `newrelic` — New Relic Alerts webhook format
- `opsgenie` — OpsGenie webhook format
- `zabbix` — Zabbix webhook format
- `generic` — flexible fallback format for custom integrations

Driver selection:

- auto-detect tries each driver’s `validate()`
- you can force a driver via `/alerts/webhook/<driver>/`

### Data model (high level)

- `Alert` — the normalized alert record (fingerprint + status + severity + labels/annotations + raw payload)
- `Incident` — groups related firing alerts and tracks state (open/ack/resolved/closed)
- `AlertHistory` — an audit trail of state transitions (created/resolved/refired)

Business logic lives in `apps/alerts/services.py` (`AlertOrchestrator`, `IncidentManager`).

## Using the webhook

### 1) Alertmanager example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/alertmanager/`:

```json
{
  "version": "4",
  "receiver": "webhook",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighCPU",
        "severity": "critical",
        "instance": "server1:9090"
      },
      "annotations": {
        "summary": "High CPU usage detected",
        "description": "CPU usage is above 90%"
      },
      "startsAt": "2024-01-08T10:00:00Z",
      "fingerprint": "abc123"
    }
  ],
  "externalURL": "http://alertmanager:9093"
}
```

### 2) Grafana example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/grafana/`:

```json
{
  "receiver": "webhook",
  "status": "firing",
  "state": "alerting",
  "title": "Disk alerts",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "DiskFull",
        "severity": "warning"
      },
      "annotations": {
        "summary": "Disk is almost full"
      },
      "startsAt": "2024-01-08T10:00:00Z"
    }
  ],
  "externalURL": "http://grafana:3000"
}
```

### 3) PagerDuty example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/pagerduty/`:

```json
{
  "event": {
    "id": "evt-123",
    "event_type": "incident.triggered",
    "resource_type": "incident",
    "occurred_at": "2024-01-08T10:00:00Z",
    "data": {
      "id": "INC-456",
      "type": "incident",
      "html_url": "https://myorg.pagerduty.com/incidents/INC-456",
      "number": 42,
      "status": "triggered",
      "title": "High latency on API endpoints",
      "urgency": "high",
      "service": {
        "id": "SVC-789",
        "summary": "Production API"
      }
    }
  }
}
```

### 4) Datadog example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/datadog/`:

```json
{
  "id": "123456",
  "title": "CPU High on web-server-1",
  "alert_id": "789",
  "alert_status": "Triggered",
  "alert_transition": "Triggered",
  "alert_type": "error",
  "alert_title": "CPU High",
  "event_msg": "CPU usage is above 90% on web-server-1",
  "hostname": "web-server-1",
  "priority": "P1",
  "tags": "env:production,service:web",
  "url": "https://app.datadoghq.com/monitors/789",
  "org": {
    "id": "org-123",
    "name": "MyOrg"
  }
}
```

### 5) New Relic example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/newrelic/`:

```json
{
  "account_id": 12345,
  "account_name": "Production",
  "condition_id": 67890,
  "condition_name": "High Memory Usage",
  "current_state": "open",
  "details": "Memory usage exceeded 85% threshold",
  "event_type": "INCIDENT",
  "incident_id": 111222,
  "incident_url": "https://alerts.newrelic.com/accounts/12345/incidents/111222",
  "policy_name": "Infrastructure Alerts",
  "severity": "CRITICAL",
  "timestamp": 1704711600,
  "targets": [
    {"name": "web-server-1", "type": "host"}
  ]
}
```

### 6) OpsGenie example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/opsgenie/`:

```json
{
  "action": "Create",
  "alert": {
    "alertId": "abc-123-def",
    "message": "Database connection pool exhausted",
    "tags": ["env:production", "team:backend"],
    "tinyId": "1234",
    "entity": "db-primary",
    "alias": "db-pool-alert",
    "createdAt": 1704711600000,
    "description": "Connection pool has no available connections",
    "team": "Backend Team",
    "source": "monitoring-service",
    "priority": "P1"
  },
  "integrationId": "integration-456",
  "integrationName": "Webhook Integration"
}
```

### 7) Zabbix example

`POST /alerts/webhook/` (auto-detect) or `POST /alerts/webhook/zabbix/`:

```json
{
  "event_id": "12345",
  "event_name": "High CPU load",
  "event_severity": "High",
  "event_status": "PROBLEM",
  "event_value": "1",
  "event_date": "2024.01.08",
  "event_time": "10:00:00",
  "host_name": "web-server-1",
  "host_ip": "192.168.1.100",
  "trigger_id": "67890",
  "trigger_name": "CPU load is too high",
  "trigger_severity": "High",
  "trigger_status": "PROBLEM",
  "item_name": "CPU Load",
  "item_value": "95.5",
  "alert_message": "CPU load on web-server-1 is 95.5%",
  "zabbix_url": "http://zabbix.example.com/tr_events.php?triggerid=67890"
}
```

### 8) Generic example (custom integrations)

Single alert:

```json
{
  "name": "My Alert",
  "status": "firing",
  "severity": "warning",
  "description": "Something happened",
  "labels": {"service": "api"}
}
```

Multiple alerts:

```json
{
  "source": "my-system",
  "alerts": [
    {
      "title": "My Alert",
      "state": "ok",
      "priority": "high",
      "message": "Alert description"
    }
  ]
}
```

Notes on flexibility:

- name fields: `name`, `alert_name`, `title`, `alertname`
- status fields: `status` (firing/resolved) or `state` (ok/resolved/normal => resolved)
- severity fields: `severity` (critical/warning/info) or `priority` (high => critical, low => info)

## Webhook responses

Successful processing returns:

```json
{
  "status": "success",
  "alerts_created": 1,
  "alerts_updated": 0,
  "alerts_resolved": 0,
  "incidents_created": 1,
  "incidents_updated": 0
}
```

If some alerts fail but others succeed:

- `status` becomes `partial`
- `errors` includes a list of error strings

## Troubleshooting

### pytest shows “no such table” errors

If you see errors like `sqlite3.OperationalError: no such table: alerts_alert`, it typically means the test DB wasn’t created/migrated.

Make sure you’re using `pytest-django` and have `DJANGO_SETTINGS_MODULE=config.settings` (already configured in `pyproject.toml`).

### Driver not detected

If auto-detection can’t find a driver:

- try forcing one: `POST /alerts/webhook/alertmanager/`
- confirm your payload includes the driver’s expected signature fields

### CSRF

The webhook view is CSRF-exempt (`@csrf_exempt`) to support external systems.

## Creating alerts from health checks

The alerts app integrates with the checkers app to create alerts from health check results.

### Using the management command

```bash
# Run all checks and create alerts
python manage.py check_and_alert

# Run specific checks
python manage.py check_and_alert --checkers cpu memory disk

# Dry run (show what would happen without creating alerts)
python manage.py check_and_alert --dry-run

# Skip incident creation
python manage.py check_and_alert --no-incidents

# Add custom labels to all alerts
python manage.py check_and_alert --label env=production --label team=sre

# Output as JSON
python manage.py check_and_alert --json

# Override thresholds for all checkers
python manage.py check_and_alert --warning-threshold 60 --critical-threshold 80
```

### Programmatic usage

```python
from apps.alerts.check_integration import CheckAlertBridge
from apps.checkers.checkers import CPUChecker

# Create a bridge instance
bridge = CheckAlertBridge(
    auto_create_incidents=True,  # Create incidents for critical/warning alerts
    hostname="my-server",        # Override hostname in labels
)

# Run a single check and create an alert
checker = CPUChecker(warning_threshold=70, critical_threshold=90)
result = checker.check()
processing_result = bridge.process_check_result(result)

print(f"Alerts created: {processing_result.alerts_created}")
print(f"Incidents created: {processing_result.incidents_created}")

# Or use the convenience method
check_result, processing_result = bridge.run_check_and_alert(
    "cpu",
    checker_kwargs={"warning_threshold": 70},
    labels={"environment": "production"},
)

# Run multiple checks at once
from apps.alerts.check_integration import CheckAlertResult

result = bridge.run_checks_and_alert(
    checker_names=["cpu", "memory", "disk"],
    labels={"datacenter": "us-east-1"},
)

print(f"Checks run: {result.checks_run}")
print(f"Total alerts created: {result.alerts_created}")
```

### How it works

1. **Check execution**: Run health checkers (CPU, memory, disk, etc.)
2. **Status mapping**: CheckStatus is mapped to alert severity:
   - `CRITICAL` → `critical` severity, `firing` status
   - `WARNING` → `warning` severity, `firing` status
   - `OK` → `info` severity, `resolved` status
   - `UNKNOWN` → `warning` severity, `firing` status
3. **Alert creation**: Alerts are created or updated based on fingerprint (checker + hostname)
4. **Incident management**: Optionally create/update incidents for firing alerts
5. **Auto-resolution**: When a check returns OK, the corresponding alert is resolved

### Setting up scheduled checks

Use cron to run checks periodically:

```bash
# Run all checks every 5 minutes
*/5 * * * * cd /path/to/project && python manage.py check_and_alert --json >> /var/log/health-checks.log 2>&1

# Run only critical checks every minute
* * * * * cd /path/to/project && python manage.py check_and_alert --checkers cpu memory --json
```
