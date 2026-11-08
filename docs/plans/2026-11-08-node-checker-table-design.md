---
title: "Node Checker Table"
parent: Plans
---

# The node page's checker table: coloured, sorted, filterable

The "Checker state" table on `/console/nodes/<pk>/` prints each status as a bare lowercase
word (`ok`, `warning`, `critical`, `unknown`) in checker order. A problem row looks like a
healthy one, and a node with fifteen checkers has to be read top to bottom.

## Design

- **Colour.** The status cell becomes a tone label with its icon:
  `c-label c-tone-<tone>` plus `_tone_icon.html`, reading "OK", "Warning", "Critical",
  "Unknown". The tone comes from the existing check-status tone map in `config/tones.py`
  (add one if none exists). The whole row gets no background, only the label.
- **Order.** Worst first: critical, warning, unknown, ok, then by checker label.
- **Filter.** A `c-states` strip in the table header, like the runs list: "All",
  "Problems" (warning, critical, unknown) and "OK", each with a count. It's a
  `?checkers=problems|ok` query param on the node page. Chips are links with htmx
  attributes targeting the node page's swap target, so they work without JavaScript. An
  unknown value falls back to All. The default is All.
- **Empty filter.** "No checker matches this filter." when a chip leaves no rows.
- The value and time columns stay as they are.

## Tests

- Each status renders its label text, tone class and icon.
- Rows come out worst first.
- `?checkers=problems` shows only non-ok rows, `?checkers=ok` only ok rows, a bogus value
  shows all, and each chip shows the right count and `aria-current` on the active one.
- The empty-filter message renders.
