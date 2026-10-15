---
title: "Ops Console Primer Redesign"
parent: Plans
---

{% raw %}

# Ops console Primer redesign

## Problem

The owner looked at the console after PR 3 and called the design too poor and far from
their other projects. The PR 1 visual system was a loose nod to Apple: grey cards,
coloured pills and a left sidebar. It has no component vocabulary, so every page
invents its own rows.

## Decision

Restyle the console on GitHub's Primer design system, chosen by the owner. Primer is
built for exactly this kind of surface: lists of issues with state, labels and
metadata, and a timeline of what happened to one of them. An incident maps onto a
GitHub issue almost one to one.

The no-npm rule stands. Primer's CSS package isn't used. `console.css` is rewritten by
hand on Primer's functional tokens and component shapes.

This lands as its own PR on top of PR 3, before PR 4, so Alerts and Runs are built on
the new look.

## Tokens

CSS custom properties named after Primer's functional tokens, light and dark, from
Primer primitives:

- `--fgColor-default`, `--fgColor-muted`, `--fgColor-accent`, `--fgColor-success`,
  `--fgColor-attention`, `--fgColor-danger`, `--fgColor-done`, `--fgColor-onEmphasis`
- `--bgColor-default`, `--bgColor-muted`, `--bgColor-inset`, and the `-muted` and
  `-emphasis` backgrounds for accent, success, attention, danger, done and neutral
- `--borderColor-default`, `--borderColor-muted`, and the tone borders
- `--header-bgColor` and `--header-fgColor` for the dark top bar

Typography is Primer's: the system stack `-apple-system, BlinkMacSystemFont, "Segoe UI",
"Noto Sans", Helvetica, Arial, sans-serif`, 14px base, line height 1.5, monospace
`ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace`.
Spacing on Primer's 4px scale. Radius 6px. Labels use a full pill.

The console's tone names stay, because `config/admin_badges.py` owns them. Each maps to
a Primer colour: `critical` to danger, `warning` to attention, `info` to accent, `ok` to
success, `muted` to neutral. Resolved and closed incident states use done purple, as
GitHub shows a closed issue.

## Layout

- **Header.** A dark full-width bar: product name on the left, "Admin" and "Sign out"
  on the right.
- **Underline nav** under the header, Primer's `UnderlineNav`: Overview, Incidents with
  a `Counter` of open incidents. Later pages add tabs here. On narrow screens it scrolls
  sideways. The left sidebar goes.
- **Content** in a container capped at 1280px.

## Components

Hand-written equivalents of these Primer components, as `c-` classes:

| Primer | Use |
|---|---|
| `Box` with header and rows | every list: incidents, nodes, readiness, stage output |
| `Label` | severity and other small tags, outlined pill in the tone colour |
| `StateLabel` | incident status, filled pill with an icon: Open green, Acknowledged attention, Resolved and Closed done purple |
| `Counter` | counts in tabs and box headers |
| `Button`, primary and danger | incident actions |
| `SegmentedControl` | filter groups on the incident list |
| `Flash` | notices, the outcome of an action, the error banner, the Overview headline |
| `Timeline` | the incident timeline, a vertical rail with an icon badge per entry |
| `Blankslate` | empty states |
| `Pagination` | the list pager |
| `PageHeader` | title, state and meta line on the incident page |

## Pages

**Overview.** A `Flash` with the headline: success when nothing is open, danger when
something critical is. The readiness items sit in a `Box`. Open incidents use the same
row as the incident list. Nodes are a grid of small boxes with a state dot. The
sparkline sits in a `Box` with a header.

**Incident list.** GitHub's issue list. The box header carries "N Active" and "N
Resolved" style status links with counts, and the other filters as segmented controls
above the box. Each row: a state icon in the state colour, the title in semibold, severity
`Label`s beside it, and a muted meta line under it: `#13 opened 3 days ago · web-1 · 4
alerts`.

**Incident page.** GitHub's issue page. A `PageHeader` with the title in 32px, a muted
`#13`, then the `StateLabel` and a meta line. Below it two columns: the timeline on the
left, a sidebar on the right with sections for Actions, Severity, Nodes, Pipeline,
Alerts and the stage strip as a small list. On narrow screens the sidebar moves above
the timeline. The timeline uses Primer's rail: each entry a round badge with an icon in
the tone colour, the one-line summary beside it, and the existing disclosure for
detail. Raw JSON renders in a `bgColor-inset` box in monospace.

## Icons

Primer's look leans on Octicons. A small set ships as one SVG sprite at
`static/console/vendor/octicons.svg`, referenced with `<svg><use href>`: issue-opened,
issue-closed, check-circle, x-circle, alert, clock, server, workflow, bell, pulse,
person, chevron. Octicons are MIT licensed. The sprite is data, not code, and has no
runtime behaviour, which is why it can join `vendor/` next to htmx. `VENDOR.md` records
the Octicons version, the source and the sprite's SHA-256, and states that the sprite
is assembled from that version's individual SVG files with paths unchanged.

## Unchanged

Markup ids, htmx attributes, polling, focus restoration, the print hook and every view
stay as they are. Templates and `console.css` change, plus the sprite. The one read
model change is per-status counts for the incident list's box header, in one extra
aggregate query.
The CSP, the template guard and the contrast guard keep passing. The contrast test is
rewritten for the new token names and keeps the AA bar in both themes.

## Acceptance

- Every console page reads as a Primer surface in light and dark.
- No horizontal page scroll at 1280, 768 and 375px.
- No CSP violations.
- Existing behaviour tests pass. Tests that pin old class names are updated, never
  deleted to make room.

{% endraw %}
