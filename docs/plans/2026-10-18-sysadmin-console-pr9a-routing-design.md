---
title: "Sysadmin Console PR 9a: Routing Design"
parent: Plans
---

# Sysadmin console PR 9a: routes, channels and the network map

Domain slice 9 of `2026-10-15-sysadmin-console-foundation-design.md`, split in two. 9a moves
routes, channels and the network map to the console and cuts `PipelineDefinitionAdmin` and
`NotificationChannelAdmin` to stock. 9b takes providers, API keys and the readiness rows
that aren't about routing.

## What exists

The rules already live outside the admin:

- `PipelineDefinition.matches`, `delivery_gap` and `routed_channel` in
  `apps/orchestration/models.py`.
- `config/netmap.py`: the map as a read-time projection, with shadow detection.
- Each notify driver's `validate_config`.
- The `edit_route` and `edit_channel` intents, which still point at admin changelists.

Only the editors are new.

## Pages

### `/console/routes/`

Every lane in evaluation order: priority, name, the match in words ("severity is critical
and source is grafana", "everything" when empty), stages, channel, and a "cannot deliver"
warning from `delivery_gap`. A header link opens the map.

### `/console/routes/new/` and `/console/routes/<pk>/`

The editor: name, description, active, priority and tags. Stages are three checkboxes in
fixed order, check then analyze then notify. Channel is a select that marks inactive
channels. Match is condition rows: a field select (source, severity, status, instance,
origin, label with a key box), an op select (is, is not, in, not in) and a value box, where
in and not in take comma-separated values. "Add condition" posts back with one more row, so
the page works without JS. No rows reads "Matches everything". `created_by` defaults to the
acting user.

### `/console/routes/map/`

Renders `get_map_context()`. `config/netmap.py` takes a link builder, so the console map
links console pages and the admin map keeps working until PR 11.

### `/console/channels/`

Name, driver, active, and the lanes routed here with any delivery gap.

### `/console/channels/new/` and `/console/channels/<pk>/`

Name, driver, active and description, then the chosen driver's typed field group. The page
lists "Routed here by" with each lane's gap, as the admin does.

### Delete

Both get a confirm page that names the consequence. A channel: "These N lanes will stop
delivering", listed. A route: which traffic falls through to which lane, from the map.

## Typed driver forms

`BaseNotifyDriver` gains `config_fields: tuple[ConfigField, ...]`.

```python
ConfigField(key, label, kind, required=False, secret=False, default=None, help="")
```

Kinds: `text`, `url`, `int`, `bool`, `list` (one value per line), `template` (textarea)
and `headers` (one `Name: value` per line, always secret).

| Driver | Fields (secret marked *) |
|---|---|
| slack | webhook_url*, timeout, template |
| email | smtp_host, smtp_port, use_tls, use_ssl, username, password*, from_address, to_addresses, timeout, text_template, html_template |
| pagerduty | integration_key*, event_action, client, client_url, timeout |
| generic | endpoint*, method, headers*, timeout, payload_template, disabled |

`ChannelForm` builds its fields from the driver, then runs the driver's own
`validate_config` on the merged config. There is still one rule for a valid config.

Secrets are write-only. A secret field reads "set" or "not set" and never renders its
value. A blank submit keeps the stored value, and a "Clear" checkbox removes it. Changing
the driver drops the old driver's secrets.

Config keys the driver doesn't declare are kept on save and listed by name in Details. A
guard test fails when a driver reads a config key it doesn't declare.

## Stale saves

- Routes: `version` exists and nothing increments it. A console save is one conditional
  `UPDATE` on pk and the version the form was opened at, and it bumps the version. A lost
  race answers 409 with the current values, and nothing is written.
- Channels: a digest of the stored row, hashed like PR 8's policy digest, with the same 409.
  The digest covers secrets without rendering them.

## Cut to stock

`PipelineDefinitionAdmin` and `NotificationChannelAdmin` become stock and read-only. The
`edit_route` and `edit_channel` intents, the dashboard readiness URLs for channels and
routes, and the network map link point at the console. Reads need `view_*`, writes need
`add_*`, `change_*` or `delete_*`, as Django assigns them.

## Tests

- Per driver: form round-trip, secret keep, secret clear, unknown keys kept.
- Match rows to JSON and back, including label fields and list values.
- Stale save 409 for routes and channels.
- Delete consequences for both.
- The console guard covers the new pages.
- 403 for each missing permission.
- 100% branch coverage on changed code.

## Out of scope

Providers, API keys and the remaining readiness rows (9b). Reordering lanes by drag.
