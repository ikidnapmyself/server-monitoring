---
title: "Progress Tone Design"
parent: Plans
---

{% raw %}
# A `progress` tone, and a neutral icon for `info`

The `info` tone means two things. On runs, stages, analyses and the inbox it means "in
progress", and its `sync` arrows fit. On info-severity alerts, readiness notes such as
"Standalone", preflight info checks and analysis summaries it means "informational", and the
arrows read as something happening. The user chose to split the tone.

## Design

- `config/tones.py`: `TONES` gains `progress`. `info` keeps its meaning, informational.
- `templates/console/partials/_tone_icon.html`: `progress` shows `sync`, `info` shows the
  Octicons `info` icon. The `info` symbol is added to `static/console/vendor/octicons.svg`
  from `@primer/octicons@19.15.1/build/svg/info-16.svg`, fetched from unpkg and jsdelivr
  with matching hashes, and `VENDOR.md` records the new sprite hash.
- `static/console/console.css`: `.c-tone-progress` uses the same colour tokens as
  `.c-tone-info`, so nothing changes colour.
- Describers that word work under way switch to `progress`:
  - `apps/orchestration/describing.py`: stage `running`, `retrying`. Run `processing`,
    `ingested`, `checked`, `analyzed`, `retrying`.
  - `apps/intelligence/describing.py`: the running analysis (`RUNNING`).
  - `apps/console/queries/analyses.py`: `pending`, `running`.
  - Any chip, rank table or sort that lists tones (`apps/console/queries/nodes.py`
    `TONE_RANK`, the runs "Active" chip) learns `progress` next to `info`.
- Everything else keeps `info`.

## Tests

- The partial renders `octicon-sync` for `progress` and `octicon-info` for `info`.
- Each switched describer state reads `progress`. An info-severity alert and the
  Standalone readiness row still read `info` and show the `info` icon.
- `.c-tone-progress` exists in `console.css`, and every name in `TONES` has a
  `.c-tone-<name>` rule.
- The sprite tests in `apps/console/_tests/test_vendor.py` pass with the new symbol.
{% endraw %}
