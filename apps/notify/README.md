# Notify

This Django app provides a flexible notification delivery system for sending alerts and messages to various platforms.

It abstracts the complexity of multiple notification backends (email, Slack, PagerDuty, etc.) behind a simple, unified interface. Drivers handle platform-specific logic and configuration.

> **Note:** Notification delivery tracking (status, retries, errors) is handled by the **orchestration layer** (`apps.orchestration`). This app focuses solely on channel configuration and message dispatch.

> **Note:** For development setup (formatting, linting, testing), see the main [README](../../README.md#development).

## What's included

### Models

- `NotificationChannel` — Persistent configuration for notification channels (managed via Django Admin)
- `NotificationSeverity` — Severity levels for notifications (`critical`, `warning`, `info`, `success`)

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

### Skipping/Disabling Drivers

You can disable specific drivers globally via the `NOTIFY_SKIP` setting.

#### Skip ALL drivers (helper)

If you want to disable *every* checker (common when using the app as a pipeline controller and you want
`alerts → checkers → intelligence` without notifications), set:

```bash
export NOTIFY_SKIP_ALL=1
```

This takes precedence over `NOTIFY_SKIP`.

#### Environment Variable

```bash
# Skip network and process drivers
export NOTIFY_SKIP=network,process

# Then run checks - network and process will be skipped
uv run python manage.py check_and_alert
```

#### Django Settings

In `config/settings.py`:

```python
# Skip specific drivers
NOTIFY_SKIP = ["network", "process"]
```


### API endpoints

The app exposes REST endpoints for sending notifications:

- `POST /notify/send/` — Send notification (specify driver in payload)
- `POST /notify/send/<driver>/` — Send notification via specific driver
- `POST /notify/batch/` — Send multiple notifications in one request
- `GET /notify/drivers/` — List available drivers
- `GET /notify/drivers/<driver>/` — Get specific driver info

### Django Admin

Access the admin interface at `/admin/notify/` to manage notification channels:

- **NotificationChannel** — Create, edit, and disable notification channels
  - Configure driver type and settings
  - Store webhook URLs, API keys (as references), and other driver-specific config
  - Enable/disable channels without deleting them

### Data model (high level)

- `NotificationChannel` — persistent channel configuration (driver + config)
- `NotificationMessage` — standardized message format with title, message, severity, and metadata
- `BaseNotifyDriver` — abstract base for all notification drivers

## Using the notification system

### 0) Configure channels via Admin

The recommended approach is to configure channels via Django Admin:

1. Navigate to `/admin/notify/notificationchannel/`
2. Add a new channel (e.g., "ops-slack")
3. Select the driver type (e.g., "slack")
4. Configure driver-specific settings in the JSON config field
5. Set `is_active=True` to enable

Example channel config for Slack:
```json
{
  "webhook_url": "https://hooks.slack.com/services/T.../B.../XXX",
  "channel": "#alerts",
  "username": "UserName",
  "icon_emoji": ":rotating_light:",
  "timeout": 30
}
```

#### Email Configuration
```json
{
  "smtp_host": "smtp.example.com",
  "smtp_port": 587,
  "from_address": "alerts@example.com",
  "to_addresses": ["ops@example.com"],
  "use_tls": true,
  "use_ssl": false,
  "username": "user@example.com",
  "password": "app-password",
  "timeout": 30
}
```

#### PagerDuty Configuration
```json
{
  "integration_key": "your-pagerduty-integration-key",
  "dedup_key": "optional-deduplication-key",
  "event_action": "trigger",
  "client": "Server Maintenance",
  "client_url": "https://your-dashboard.com",
  "timeout": 30
}
```

#### Generic HTTP Configuration
```json
{
  "endpoint": "https://api.example.com/notify",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer your-api-key",
    "X-Custom-Header": "value"
  },
  "timeout": 30,
  "payload_template": {
    "alert": "{title}",
    "body": "{message}",
    "level": "{severity}"
  }
}
```

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

Selection priority when invoking notifications from the orchestration pipeline
or management commands

- If the pipeline payload's `notify_driver` matches a `NotificationChannel.name` in the
  database (and that channel is `is_active=True`), the channel's stored `driver` and
  `config` will be used (this allows choosing named channels configured via Admin).
- If no `notify_driver` is provided in the payload, the orchestration layer will select
  the first active `NotificationChannel` ordered by name and use its driver and config.
- If neither of the above applies, the orchestration pipeline treats `notify_driver` as
  a driver key (for example, `slack`, `email`, `generic`) and uses the provided
  `notify_config` from the payload or default behavior.

This ordering lets you prefer centrally-managed channels (via Admin) while still
allowing ad-hoc driver usage from scripts and management commands.

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

## Using NotificationChannel in code

```python
from apps.notify.models import NotificationChannel
from apps.notify.drivers.slack import SlackNotifyDriver
from apps.notify.drivers.base import NotificationMessage

# Get an active channel by name
channel = NotificationChannel.objects.get(name="ops-slack", is_active=True)

# Create message
message = NotificationMessage(
    title="CPU Alert",
    message="CPU usage exceeded 90%",
    severity="critical",
)

# Get driver and send
driver = SlackNotifyDriver()  # Or use a registry to get driver by channel.driver
result = driver.send(message, channel.config)
```

## Architecture notes

### Orchestration integration

This app is the **final stage** in the pipeline: `alerts → checkers → intelligence → notify`

- **Delivery tracking** is handled by `apps.orchestration` (via `StageExecution`)
- **Channel configuration** is stored in `NotificationChannel` model
- **Message dispatch** is handled by drivers
- The orchestrator calls this app's dispatch function and records results in `StageExecution.output_snapshot`

### Design principles

- Drivers are stateless and thread-safe
- Channel configuration is stored in `NotificationChannel`, passed to drivers at send time
- All drivers normalize to the same result format
- Failures return structured error information (captured by orchestrator)
- Each driver independently handles retries, rate-limiting, etc.
- **No separate notification logs** — the orchestration layer tracks all delivery attempts

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

