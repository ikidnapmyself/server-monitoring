# Notify

This Django app provides a flexible notification delivery system for sending alerts and messages to various platforms.

It abstracts the complexity of multiple notification backends (email, Slack, PagerDuty, etc.) behind a simple, unified interface. Drivers handle platform-specific logic and configuration.

> **Note:** For development setup (formatting, linting, testing), see the main [README](../../README.md#development).

## What's included

### Drivers (notification backends)

Drivers live in `apps/notify/drivers/` and are responsible for:

- validating driver configuration (`validate_config()`)
- sending notifications to their backend (`send()`)
- normalizing results into a common format

Built-in drivers:

- `email` — SMTP email notifications
- `slack` — Slack workspace integration via webhooks
- `pagerduty` — PagerDuty incident creation
- `generic` — flexible fallback for custom integrations

### Management commands

- `python manage.py list_notify_drivers` — List available notification drivers and configuration requirements
- `python manage.py test_notify <driver>` — Test notification delivery to a specific backend

### API endpoints

The app exposes REST endpoints for sending notifications:

- `POST /notify/send/` — Send notification (specify driver in payload)
- `POST /notify/send/<driver>/` — Send notification via specific driver
- `POST /notify/batch/` — Send multiple notifications in one request
- `GET /notify/drivers/` — List available drivers
- `GET /notify/drivers/<driver>/` — Get specific driver info

### Data model (high level)

- `NotificationMessage` — standardized message format with title, message, severity, and metadata
- `BaseNotifyDriver` — abstract base for all notification drivers

## Using the notification system

### 1) List available drivers

```bash
# Show available drivers
python manage.py list_notify_drivers

# Show detailed configuration requirements
python manage.py list_notify_drivers --verbose
```

### 2) Test notification delivery

```bash
# Test email driver
python manage.py test_notify email \
    --smtp-host smtp.gmail.com \
    --from-address alerts@example.com

# Test Slack driver
python manage.py test_notify slack \
    --webhook-url https://hooks.slack.com/services/T00000000/B00000000/XXXXXXX \
    --channel "#alerts"

# Test PagerDuty driver
python manage.py test_notify pagerduty \
    --integration-key your-integration-key

# Test with custom message
python manage.py test_notify slack \
    --webhook-url https://hooks.slack.com/... \
    --title "Custom Alert" \
    --message "Something happened" \
    --severity critical
```

### 3) Creating a notification message programmatically

```python
from apps.notify.drivers.base import NotificationMessage

message = NotificationMessage(
    title="CPU Alert",
    message="CPU usage exceeded 90% threshold",
    severity="critical",
    channel="devops-alerts",
    tags={"environment": "production", "service": "api"},
    context={"current_usage": 95.2, "threshold": 90},
)
```

### 4) Sending via a specific driver

```python
from apps.notify.drivers.slack import SlackNotifyDriver

driver = SlackNotifyDriver()
config = {
    "webhook_url": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXX",
    "channel": "#alerts"
}

result = driver.send(message, config)
print(result)
# {
#     "success": True,
#     "message_id": "slack_...",
#     "metadata": {"channel": "#alerts", ...}
# }
```

### 5) Sending via email

```python
from apps.notify.drivers.email import EmailNotifyDriver

driver = EmailNotifyDriver()
config = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "from_address": "alerts@example.com",
    "use_tls": True,
    "username": "user@example.com",
    "password": "app-password",
}

result = driver.send(message, config)
```

### 6) Sending via PagerDuty

```python
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver

driver = PagerDutyNotifyDriver()
config = {
    "integration_key": "your-pagerduty-integration-key",
}

result = driver.send(message, config)
```

## Adding a new notification driver

1. Create a new file in `apps/notify/drivers/` (e.g., `myservice.py`)
2. Subclass `BaseNotifyDriver`
3. Implement `validate_config()` and `send()` methods
4. Add to `apps/notify/drivers/__init__.py`

Example:

```python
from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

class MyServiceNotifyDriver(BaseNotifyDriver):
    name = "myservice"
    
    def validate_config(self, config):
        return "api_key" in config
    
    def send(self, message, config):
        # Your implementation here
        return {
            "success": True,
            "message_id": "...",
            "metadata": {...}
        }
```

## Architecture notes

- Drivers are stateless and thread-safe
- Configuration is passed at send time, not stored
- All drivers normalize to the same result format
- Failures return structured error information
- Each driver independently handles retries, rate-limiting, etc.

## API usage examples

### Send notification via API

```bash
# Send via Slack
curl -X POST http://localhost:8000/notify/send/slack/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "High CPU Alert",
    "message": "CPU usage exceeded 90%",
    "severity": "critical",
    "channel": "#alerts",
    "config": {
      "webhook_url": "https://hooks.slack.com/services/T.../B.../XXX"
    }
  }'

# Send via Email
curl -X POST http://localhost:8000/notify/send/email/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Database Alert",
    "message": "Connection pool exhausted",
    "severity": "warning",
    "config": {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "from_address": "alerts@example.com",
      "to_addresses": ["ops@example.com"],
      "use_tls": true,
      "username": "user@example.com",
      "password": "app-password"
    }
  }'

# Send via PagerDuty
curl -X POST http://localhost:8000/notify/send/pagerduty/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Service Down",
    "message": "API server is not responding",
    "severity": "critical",
    "tags": {"service": "api", "environment": "production"},
    "config": {
      "integration_key": "your-pagerduty-integration-key"
    }
  }'
```

### Batch notifications

```bash
curl -X POST http://localhost:8000/notify/batch/ \
  -H "Content-Type: application/json" \
  -d '{
    "notifications": [
      {
        "driver": "slack",
        "title": "Alert 1",
        "message": "First notification",
        "severity": "warning",
        "config": {"webhook_url": "https://hooks.slack.com/..."}
      },
      {
        "driver": "email",
        "title": "Alert 2", 
        "message": "Second notification",
        "severity": "info",
        "config": {"smtp_host": "smtp.example.com", "from_address": "alerts@example.com"}
      }
    ]
  }'
```

### List available drivers

```bash
# List all drivers
curl http://localhost:8000/notify/drivers/

# Get specific driver info
curl http://localhost:8000/notify/drivers/slack/
```

### API response format

**Success response:**
```json
{
  "status": "success",
  "driver": "slack",
  "message_id": "slack_1a2b3c4d",
  "metadata": {
    "channel": "#alerts",
    "severity": "critical"
  }
}
```

**Error response:**
```json
{
  "status": "error",
  "driver": "slack",
  "message": "Invalid Slack configuration (valid webhook_url required)"
}
```

**Batch response:**
```json
{
  "status": "partial",
  "total": 3,
  "success_count": 2,
  "error_count": 1,
  "results": [
    {"index": 0, "success": true, "driver": "slack", "message_id": "..."},
    {"index": 1, "success": true, "driver": "email", "message_id": "..."},
    {"index": 2, "success": false, "driver": "pagerduty", "error": "..."}
  ]
}
```
