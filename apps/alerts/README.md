# Alerts

This Django app provides alert ingestion and incident orchestration.

It exposes a simple webhook endpoint that accepts JSON payloads from multiple alert sources (Alertmanager, Grafana, and a generic/fallback format), normalizes them into a common schema, and stores them as `Alert` + `Incident` records.

## Development (format/lint/test)

This repo uses `uv` for dependency management and a small, consistent dev-tooling stack configured in `pyproject.toml`:

- **Black** for formatting
- **Ruff** for linting + import sorting
- **pytest + pytest-django** for tests
- **mypy + django-stubs** (optional) for type-checking

Common commands:

```bash
# Install runtime + dev tools
uv sync --extra dev

# Format
uv run black .

# Lint (and auto-fix imports where possible)
uv run ruff check . --fix

# Tests
uv run pytest

# Optional: type-check
uv run mypy .
```

## What’s included

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

### 3) Generic example (custom integrations)

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

