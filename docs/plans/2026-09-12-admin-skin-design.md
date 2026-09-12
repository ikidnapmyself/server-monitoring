---
title: "Admin Ops Skin"
parent: Plans
---

# Admin ops skin

## Problem

The ops console runs on stock Django 5.2 admin with no theme layer. Three custom
pages (`templates/admin/dashboard.html`, `map.html`, `policy_overview.html`) plus
`templates/admin/alerts/node/change_form.html` carry their own inline `<style>`
blocks and hardcoded hex colors. Roughly 90 literal hexes are spread across the
five `admin.py` files and those templates, most of them inside 60 `format_html`
calls emitting inline-styled status text.

Three complaints, confirmed with the operator:

1. The chrome looks dated and unbranded.
2. The custom pages look bolted on: off-palette colors, dark mode broken per file.
3. Changelists are hard to scan.

## Approach

A design-token layer over the stock admin. Django 5.2's admin is fully driven by
CSS custom properties with `[data-theme]` switching, so the skin redefines those
variables rather than fighting selectors, then adds a second layer for what Django
has no variable for: row density, the badge system, card shells.

Rejected alternatives:

- **django-unfold.** Highest visual ceiling, but it requires swapping the admin
  site base class, converting all 14 `ModelAdmin` classes, rewriting the custom
  templates against its block structure, and reconciling its action system with
  the 12 `django_object_actions` call sites. Too much blast radius against 2722
  lines of admin code for a skin.
- **Custom pages only.** A strict subset of this design. Fixes the bolted-on
  complaint and nothing else.

Visual direction is an ops console: dark-first, dense, monospace for identifiers,
saturated status colors. Light mode stays supported because Django's theme toggle
lives in the header and silently breaking it is worse than supporting both.

## Files to add or change

| Path | Purpose |
|---|---|
| `static/admin/css/ops.css` | new. The whole skin. Loaded after Django's `base.css`. |
| `templates/admin/base_site.html` | new. Overrides `extrastyle` to pull in `ops.css`. |
| `config/admin_badges.py` | new. `badge(text, tone)` helper, the single source of status color. |
| `config/settings.py` | add `STATICFILES_DIRS = [BASE_DIR / "static"]`; it does not exist yet. |
| `templates/admin/dashboard.html` | move its 579-line inline `<style>` into `ops.css`, retargeted at tokens. |
| `templates/admin/map.html` | same, drop inline hex fallbacks. |
| `templates/admin/policy_overview.html` | replace inline `style=` attributes with classes. |
| `templates/admin/alerts/node/change_form.html` | three inline-styled badge spans collapse to `ops-badge--{{ freshness_status }}`. |
| `apps/*/admin.py` | migrate status-rendering `format_html` sites to `badge()`. |

## Public interface

```python
def badge(text: str, tone: str) -> SafeString:
    """Render one status pill. tone in {critical, warning, info, ok, muted}."""
```

Unknown tone raises `ValueError` rather than silently emitting an unclassed span.

`build_incident_rows` (`apps/alerts/node_overview.py`) stops returning `.color` as
a hex string and returns `.tone` instead.

## Palette and type

- Surfaces: near-black base, one step up for cards and table headers, hairlines
  instead of heavy borders.
- Accent: one cool accent for links, focus rings and primary buttons, replacing
  Django's `#79aec8`.
- Status ramp: five semantic tokens (`critical`, `warning`, `info`, `ok`, `muted`),
  each with an `-fg` and `-bg` pair so badges stay legible in both themes. These
  replace the ad-hoc `#dc3545` / `#28a745` / `#ffc107` / `#6c757d` / `#17a2b8`.
- Type: system UI stack for chrome; a monospace stack for `trace_id`, `run_id`,
  `instance_id`, fingerprints and JSON.
- Density: tighter changelist rows, larger button targets, a 4px spacing scale.

## Surfaces

**Changelists and filters.** Sticky table header. Hairline row separators instead
of zebra striping. Monospace with tabular numerals on ID, trace and timestamp
columns; numerics right-aligned. The filter sidebar becomes a panel with
collapsible groups. Sort headers get a real affordance. Search box and action bar
merge into one toolbar row. Compact pagination.

**Forms and buttons.** One button system: primary, secondary, danger, with
consistent height, radius and visible focus rings. Covers the submit row,
`object-tools-items` (where the `django_object_actions` buttons land) and the
delete confirmation. Fieldsets become cards with a quiet header. The JSON widget
gets the monospace stack and a dark editor theme.

**Dashboard and nav.** The dashboard already uses semantic classes
(`readiness-card readiness-{{ status }}`, `severity-badge critical`), so its styles
move into `ops.css` largely intact. Readiness cards gain a status left-edge and a
clearer hover state. The `get_app_list()` sidebar gets section headers that read as
sections, plus current-page highlighting.

**Out of scope.** The login page, `admindocs`, and any change to layout or
information architecture. Same fields, same columns, same actions, same URLs.

## Testing

Six existing assertions couple to hex values and must be rewritten to assert on
tone names and badge classes:

- `config/_tests/test_policy_overview_view.py` lines 56, 66, 114, 115, 145
- `apps/alerts/_tests/test_node_overview.py` line 470

That is a better test either way: it checks the semantic decision, not the paint.

New coverage:

- `config/_tests/test_admin_badges.py` — each tone renders its class, text is
  escaped, an unknown tone raises.
- `config/_tests/test_dashboard_render.py` and `test_policy_overview_view.py`
  extended to assert `ops.css` is linked and that badges carry classes.
- A guard test asserting no `style="color:#` or `background-color:#` remains in
  `apps/*/admin.py` or under `templates/admin/`. This is what stops the hexes
  creeping back.

`ops.css` is not Python and does not move the coverage number.

## Acceptance criteria

1. `uv run pytest` green, 100% branch coverage on changed Python.
2. No literal hex color remains in `apps/*/admin.py` or `templates/admin/`.
3. Dashboard, an incident changelist, a node change form and the policy overview
   all render correctly in both light and dark themes.
4. The `django_object_actions` buttons still appear on the Node changelist and
   change form.
5. No change to any admin URL, registered model, field set or action.
