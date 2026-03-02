# Notify

This Django app provides a flexible notification delivery system for sending alerts and messages to various platforms.

It abstracts the complexity of multiple notification backends (email, Slack, PagerDuty, etc.) behind a simple, unified interface. Drivers handle platform-specific logic and configuration.

> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (NOTIFY stage).

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

#### `list_notify_drivers`

```bash
# List available notification drivers
uv run python manage.py list_notify_drivers

# Show detailed configuration requirements (required/optional fields per driver)
uv run python manage.py list_notify_drivers --verbose
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--verbose` | flag | — | Show required/optional config fields per driver |

#### `test_notify`

Send a test notification to verify driver or channel configuration.

**Default mode is interactive** — a wizard guides you through channel selection, message
options, and lets you retry or switch channels without restarting the command.

```bash
# Launch the interactive wizard (default)
uv run python manage.py test_notify
```

##### Interactive wizard

```
$ manage.py test_notify

=== Test Notification Wizard ===

Select a channel:
  1. ops-slack (slack)
  2. oncall-email (email)
  3. Configure a new driver manually
Enter choice: 1

Title [Test Alert]:
Message [This is a test notification from the notify app.]:
Severity (critical/warning/info/success) [info]: warning

Sending test notification via ops-slack (provider=slack)...
Notification sent successfully!
  Message ID: abc-123

What next?
  1. Retry with different message options
  2. Switch to a different channel
  3. Done — exit
Enter choice: 3
```

Key behaviors:
- **Channel discovery** — lists active `NotificationChannel` records from the database
- **Configure new** — prompts for driver type and driver-specific config fields
- **Defaults in brackets** — press Enter to accept, type to override
- **Adjust loop** — retry with different title/message/severity or switch channels

##### Non-interactive mode

Use `--non-interactive` for CI/scripting or when you prefer CLI flags:

```bash
# Test using first active DB channel
uv run python manage.py test_notify --non-interactive

# Test a specific driver with flags
uv run python manage.py test_notify slack --non-interactive \
  --webhook-url https://hooks.slack.com/services/T.../B.../XXX

# Test a named DB channel
uv run python manage.py test_notify ops-slack --non-interactive

# Custom message
uv run python manage.py test_notify slack --non-interactive \
  --webhook-url https://hooks.slack.com/services/T.../B.../XXX \
  --title "Deploy Alert" \
  --message "Deployment started" \
  --severity warning

# JSON config (advanced)
uv run python manage.py test_notify slack --non-interactive \
  --json-config '{"webhook_url": "https://hooks.slack.com/...", "channel": "#alerts"}'
```

##### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--non-interactive` | flag | — | Skip wizard, use CLI flags |
| `driver` (positional) | str | first active channel | Driver name or DB channel name |
| `--title` | str | `Test Alert` | Notification title |
| `--message` | str | default test message | Notification body |
| `--severity` | choice | `info` | `critical`, `warning`, `info`, or `success` |
| `--channel` | str | `default` | Destination channel/recipient |
| `--json-config` | str | — | Full driver config as JSON string |
| `--smtp-host` | str | — | SMTP host (email driver) |
| `--smtp-port` | int | `587` | SMTP port (email driver) |
| `--from-address` | str | — | Sender address (email driver) |
| `--use-tls` | flag | — | Enable TLS for SMTP (email driver) |
| `--webhook-url` | str | — | Webhook URL (slack driver) |
| `--integration-key` | str | — | Integration key (pagerduty driver) |
| `--endpoint` | str | — | API endpoint (generic driver) |
| `--api-key` | str | — | API key (generic driver) |

### Skipping/Disabling Drivers

You can disable specific drivers globally via the `NOTIFY_SKIP` setting.

#### Skip ALL drivers (helper)

If you want to disable *every* notification driver (common when using the app as a pipeline controller
without notifications), set:

```bash
export NOTIFY_SKIP_ALL=1
```

This takes precedence over `NOTIFY_SKIP`.

#### Environment Variable

```bash
# Skip specific notification drivers
export NOTIFY_SKIP=email,pagerduty

# Then run pipeline - email and pagerduty notifications will be skipped
uv run python manage.py run_pipeline --sample
```

#### Django Settings

In `config/settings.py`:

```python
# Skip specific drivers
NOTIFY_SKIP = ["email", "pagerduty"]
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

# Show detailed configuration requirements (required/optional fields)
python manage.py list_notify_drivers --verbose
```

### 2) Test notification delivery

```bash
# Interactive wizard (recommended) — discovers channels, lets you retry
python manage.py test_notify

# Non-interactive (CI/scripting)
python manage.py test_notify slack --non-interactive \
    --webhook-url https://hooks.slack.com/services/T00000000/B00000000/XXXXXXX

python manage.py test_notify email --non-interactive \
    --smtp-host smtp.gmail.com \
    --from-address alerts@example.com
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

