# Installation Docs Update — Design

> **Status:** Approved 2026-02-23

**Goal:** Update `docs/Installation.md` to include shell alias setup, pipeline workflow examples with aliases, fix section numbering, and cross-link with `bin/README.md`.

**Context:** `docs/Installation.md` is missing alias setup (section 3 skipped), has no pipeline workflow examples, and the "Common commands" section only shows raw `uv run` commands. `bin/README.md` has the alias table but no workflow examples. They should cross-link, not merge.

## Files to Update

| File | Changes |
|------|---------|
| `docs/Installation.md` | Fix section numbering (1,2,4,4,5 → 1-6), add alias setup section, add pipeline workflow section, update common commands with alias equivalents, add cross-links |
| `bin/README.md` | Add cross-link to Installation.md |

## Installation.md Changes

1. **Fix numbering**: 1) Quick install, 2) Cron, 3) Shell aliases (new), 4) Interactive CLI, 5) Manual installation, 6) Common commands, 7) Pipeline workflow with aliases (new)
2. **Section 3 — Shell aliases**: Setup instructions, link to bin/README.md for full table
3. **Section 7 — Pipeline workflow**: sm-setup-instance → sm-run-pipeline --dry-run → sm-run-pipeline, plus JSON file and sample examples
4. **Update section 6 — Common commands**: Show alias equivalents alongside raw commands
5. **Cross-links**: Reference bin/README.md for alias table

## bin/README.md Changes

Add line after the alias table: "See `docs/Installation.md` for setup guide and pipeline workflow examples."