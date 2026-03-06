---
title: "Interactive test_notify — Design"
parent: Plans
nav_exclude: true
---
# Interactive test_notify — Design

## Problem

`test_notify` requires users to remember CLI flags (`--webhook-url`, `--smtp-host`, etc.),
offers no way to discover existing channels, and provides no retry/adjust loop after sending.
Users must re-run the command from scratch to test again with different parameters.

## Decision

**Approach A: Wizard overlay on existing command.** Add an interactive wizard layer on top
of the existing `test_notify` command. Current flag-based logic stays untouched — the wizard
collects the same parameters interactively and delegates to the existing send flow.

- Default behavior: interactive wizard (when no `--non-interactive` flag)
- `--non-interactive`: bypasses wizard, uses current flag-based behavior (for CI/scripting)

## Interactive Flow

```
$ manage.py test_notify

=== Test Notification Wizard ===

Active notification channels:
  1) ops-slack (slack) — [setup_wizard] slack channel
  2) oncall-email (email) — [setup_wizard] email channel
  3) Configure a new driver manually

Select channel [1]: 1

  Title [Test Alert]:
  Message [This is a test notification...]:
  Severity (critical/warning/info/success) [info]: warning

Sending test notification to ops-slack (slack)...

✓ Notification sent successfully!
  Message ID: abc-123
  Metadata: {"channel": "#alerts"}

What next?
  1) Retry with changes
  2) Send to a different channel
  3) Done

Select [3]: 1

  Title [Test Alert]:
  Severity [warning]:

Sending...
✓ Sent!

What next? [3]: 3
```

### Key behaviors

- **Channel discovery**: Lists active `NotificationChannel` records, plus "configure new" option
- **"Configure new"**: Prompts for driver type, then driver-specific config fields
- **Defaults in brackets**: Enter accepts default, type to override
- **Result display**: Success/failure, message_id, metadata, error details
- **Adjust loop**: Change title/message/severity, or switch channels
- **`--non-interactive`**: Current flag-based behavior, unchanged

## Command Interface

### Preserved flags (all work in `--non-interactive` mode)

- `driver` (positional), `--title`, `--message`, `--severity`, `--channel`
- `--json-config`, `--smtp-host`, `--smtp-port`, `--from-address`, `--use-tls`
- `--webhook-url`, `--integration-key`, `--endpoint`, `--api-key`

### New flag

- `--non-interactive` — Skip wizard, use flag-based behavior. Required for CI/scripting.

### Internal structure

```python
def handle(self, *args, **options):
    if options["non_interactive"]:
        self._handle_non_interactive(**options)  # existing logic, renamed
    else:
        self._handle_interactive(**options)       # new wizard

# New private methods:
def _handle_interactive(self, **options)
def _select_channel(self) -> tuple[str, dict]
def _configure_new_driver(self) -> tuple[str, dict]
def _prompt_message_options(self, defaults) -> dict
def _send_and_show_result(self, driver_name, config, message) -> dict
def _post_send_loop(self, driver_name, config, message) -> str  # "retry" | "switch" | "done"
```

Reuses existing `_build_*_config` helpers adapted to prompt interactively.

## Testing

**File**: `apps/notify/_tests/test_test_notify.py`

| Area | Tests |
|------|-------|
| Non-interactive mode | Existing flag-based behavior with `--non-interactive` |
| Channel selection | Lists active channels, empty state, valid selection |
| Configure new driver | Prompts for each driver type's config fields |
| Message prompts | Defaults accepted, custom values collected |
| Send result display | Success and failure rendering |
| Post-send loop | Retry, switch channel, and done paths |
| Edge cases | No active channels, invalid input |

**Mocking**: `unittest.mock.patch("builtins.input")` + mock driver `send()`.
**Coverage**: 100% branch coverage on new interactive code.

## Documentation Updates

| File | Changes |
|------|---------|
| `apps/notify/README.md` | Update test_notify section: interactive mode, `--non-interactive`, wizard example |
| `docs/Setup-Guide.md` | Add "Testing Notifications" section after pipeline setup |
| `apps/notify/agents.md` | Add interactive wizard contract, `--non-interactive` for automation |