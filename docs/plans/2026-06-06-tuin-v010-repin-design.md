---
title: "Re-pin vendored tuin to v0.1.0"
parent: Plans
---

# Re-pin vendored tuin to v0.1.0 — Design

**Status:** approved design
**Date:** 2026-06-06
**Supersedes the version pin in:** `docs/plans/2026-06-02-tuin-cli-ui-design.md` /
`-ui.md` (those remain immutable historical records of the v0.3.0-era
implementation).

## Context

The `bin/cli.sh` tuin integration (banner/section/menu/confirm/input conversions
plus the `pickers.sh` arrow-key picker subsystem built on `tuin_choose`) is
functionally complete. It was vendored against a development version labeled
`v0.3.0`.

Upstream [`ikidnapmyself/tuin`](https://github.com/ikidnapmyself/tuin) now
publishes exactly one tag — **`v0.1.0`** — as its first stable release. The
`v0.3.0` tag no longer exists upstream, so `vendor_tuin`'s pinned `curl` URL
would 404.

The released `v0.1.0` `tuin.sh` is **byte-identical** to the vendored copy except
for two version-marker lines, and exposes the same function surface
(`tuin_banner`, `tuin_section`, `tuin_menu`, `tuin_confirm`, `tuin_input`,
`tuin_choose`, `tuin_spin`, `tuin_version`). No API adaptation is required; this
is a pure version-label correction.

## Changes

1. **`bin/lib/tuin.sh`** — re-vendor from the `v0.1.0` tag via the project's own
   `vendor_tuin` helper. Net diff: `# Version: 0.3.0` → `0.1.0` and
   `_TUIN_VERSION="0.3.0"` → `"0.1.0"`.
2. **`bin/lib/tuin_vendor.sh`** — `TUIN_VERSION="${TUIN_VERSION:-v0.3.0}"` →
   `v0.1.0`.
3. **`docs/Security.md`** — the live supply-chain note's "tag-pinned (`v0.3.0`)"
   → `v0.1.0`.

## Explicitly out of scope

- `docs/plans/2026-06-02-tuin-cli-ui*.md` — immutable historical records; they
  correctly describe the v0.3.0-era state at the time they were written.
- Third-party vendored bats files under `bin/tests/test_helper/` whose own
  unrelated `0.3.0` version strings must not be touched.
- Any behavior change to the CLI, pickers, or tests.

## Verification

- `bash -c 'source bin/lib/tuin.sh; tuin_version'` prints `0.1.0`.
- `TUIN_VERSION=v0.1.0 bash -c 'source bin/lib/tuin_vendor.sh; vendor_tuin'`
  succeeds (URL resolves, `Version:` sanity check passes) and
  `git diff bin/lib/tuin.sh` shows only the two marker lines.
- bats suite green (incl. `test_tuin_vendor.bats`, `test_pickers.bats`,
  `test_cli_seteov.bats`).
- `shellcheck bin/cli.sh bin/cli/*.sh bin/lib/tuin_vendor.sh bin/lib/pickers.sh`
  clean (vendored `tuin.sh` excluded).

## Done

- Re-pin committed on the `design/tuin-cli-ui` branch.
- Branch finished via a PR to `main` (no direct pushes to `main`).