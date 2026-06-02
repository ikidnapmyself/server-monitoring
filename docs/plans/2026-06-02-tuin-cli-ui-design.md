---
title: "tuin as the bin/cli.sh UI"
parent: Plans
---

# tuin as the `bin/cli.sh` UI — Design

**Status:** approved design, pending implementation
**Date:** 2026-06-02
**Source spec:** `tuin-integration-guide.md` (repo root), revised collaboratively via brainstorming.

## Goal

Replace the hand-rolled `select`/`read` interactive UI in `bin/cli.sh` and its
five Django-wrapper submenus with [tuin](https://github.com/ikidnapmyself/tuin)
v0.3.0 primitives (`tuin_banner`, `tuin_section`, `tuin_menu`, `tuin_confirm`,
`tuin_input`, `tuin_choose`, `tuin_spin`). tuin is a single-file, zero-dependency,
pure-bash TUI library (bash 3.2+) with built-in non-TTY/CI fallbacks. tuin is
used **by default** — not optional, no install prompt.

This design revises the original integration guide in one substantive way: it
adds a small **picker subsystem** so "type the name" flows become "arrow-select
the name," using `tuin_choose` against option lists parsed from existing
`manage.py` output. No Django-side changes.

## Scope

**Convert (the Django-wrapper CLI layer):**

- `bin/cli.sh` — `show_banner`, `confirm_and_run`, `run_command`, the main menu,
  the `main()` dispatcher.
- `bin/cli/health.sh`, `bin/cli/pipeline.sh`, `bin/cli/intelligence.sh`,
  `bin/cli/notifications.sh`, `bin/cli/cluster.sh` — `select` menus and `read -p`
  prompts.

**Leave untouched (not Django wrappers):**

- `bin/install.sh` and `bin/install/*.sh` (except the `ensure_tuin` self-heal line)
- `bin/update.sh` and `bin/lib/update.sh`
- `bin/lib/prompt.sh` (installer prompts)
- `bin/cli/install_menu.sh`, `bin/cli/update.sh`
- `bin/lib/colors.sh` — still used for the `$GREEN`/`$RED` result lines in `run_command`.

## Decisions made during brainstorming

| Question | Decision |
|---|---|
| Faithfulness | Rethink the UX where tuin enables it — not a pure 1:1 port. |
| Pickers | Add `tuin_choose` pickers for **single-checker**, **single pipeline-definition**, and **static notify-driver** flows. |
| Option source | **Parse human `manage.py` output in shell** — no Django changes (stays in `bin/` scope). |
| Multi-item flows | tuin has no multi-select → "Run specific checkers" and Cluster "specific checkers" **stay free-text** `tuin_input`. |
| Picker cancel | `tuin_choose` has no Back → prepend an explicit **`← Cancel`** entry to every picker. |
| Edge cases | Pipeline picker lists **all** definitions (incl. inactive, exact names). On empty list or command failure: **print a message and return to the submenu** — never fabricate, never silently fall through. |
| Helper location | Picker/parse helpers live in a **new `bin/lib/pickers.sh`** so the parsers are unit-testable in isolation. |

## Why this is the lean approach

- **Vendored single file** rides the existing `bin/update.sh` `git pull` path —
  no new transport or update mechanism.
- **Parse-in-shell** keeps option sourcing inside `bin/` scope, matching
  `bin/AGENTS.md` ("invoke `manage.py` and parse its output"). The more robust
  alternative — adding machine-readable flags (`check_health --list --quiet`,
  `show_pipeline --names`) — was considered and **rejected** because it expands
  scope into `apps/checkers` + `apps/orchestration` (command code + tests +
  docs), which the scope-discipline rules in `CLAUDE.md` warn against. Revisit
  only if an output marker actually changes.
- **Reuse** `confirm_and_run`/`run_command` rather than introducing new run
  machinery.

## tuin API used (v0.3.0 — verified present)

| Function | Behavior relevant here |
|---|---|
| `tuin_banner <title>` | Boxed banner (Unicode/ASCII per locale). |
| `tuin_section <heading>` | `═══ heading ═══` divider. |
| `tuin_menu <title> <opt…>` | Looping menu, auto-appends Back. Sets `$TUIN_REPLY` on action pick (return 0); non-zero on Back/ESC/Ctrl-C. Back label via `TUIN_MENU_BACK`. |
| `tuin_choose <opt…>` | One-shot arrow picker; prints selection to **stdout**; type-ahead filter at ≥10 items; non-TTY reads a 1-indexed line. Returns 130 on Ctrl-C. |
| `tuin_confirm <prompt> [y\|n]` | y/n; 0=yes, 1=no, 130=Ctrl-C; default shown capitalized. |
| `tuin_input <prompt> [default] [regex]` | Line input; value to **stdout** (capture with `$(…)`); optional default + regex. |
| `tuin_spin <label> -- <cmd…>` | Runs command with spinner; passes through output; returns command exit code. |

**Gotchas:** capture `tuin_input`/`tuin_choose` with `$(…)`; keep
`tuin_menu`/`tuin_confirm` in `if`/`while` conditions (safe under `set -e`);
`$TUIN_REPLY` is the only global tuin writes (replaces `select`'s `$REPLY`).

## `set -e` interaction

`bin/cli.sh` runs under `set -e`. `tuin_menu`/`tuin_confirm`/`tuin_choose`
intentionally return non-zero (Back/No/Cancel). All such calls go in `if`/`while`
condition position, which `set -e` does not treat as an abort. The pre-existing
`eval "$cmd"` abort behavior in `confirm_and_run`/`run_command` is preserved
unchanged.

## Component design

### Vendoring

- `bin/lib/tuin.sh` — committed copy pinned to `v0.3.0` (keep its version marker).
- `bin/lib/tuin_vendor.sh` — `TUIN_VERSION` + `vendor_tuin` (re-download) +
  `ensure_tuin` (fetch only if missing). Source order/idiom mirrors the existing
  `uv` vendoring pattern.
- `bin/install.sh` — add `source "$SCRIPT_DIR/lib/tuin_vendor.sh"` + `ensure_tuin`
  near the other lib sources (self-heal).
- `bin/update.sh` — no change; `git pull` carries the committed `tuin.sh`. To bump:
  edit `TUIN_VERSION`, run `vendor_tuin`, commit the new `tuin.sh`.

### Core helpers (`bin/cli.sh`)

- `show_banner` → `tuin_banner "Server Maintenance CLI"` + aliases tip.
- `confirm_and_run` → `tuin_section "Command to run"` + `tuin_confirm "Run this command?" n`.
- `run_command` → unchanged logic; optionally `tuin_spin "$description" -- bash -c "$cmd"`.

### Main menu

`main_menu_loop`: `TUIN_MENU_BACK="Exit"`; `while true` showing the banner and a
`tuin_menu "Select an option" …`; `case $TUIN_REPLY` dispatches to `*_menu`
functions; `tuin_input "Press Enter to continue" >/dev/null || true` between
actions; `else` branch prints "Goodbye!" and returns 0. `main()`'s `""` case
calls `main_menu_loop`; jump cases call the relevant `*_menu` directly (each
loops internally now).

### Submenu pattern (all five)

```
<name>_menu() {
    while true; do
        show_banner
        if tuin_menu "<Title>" "<label1>" "<label2>" …; then
            case $TUIN_REPLY in
                "<label1>") … ;;
                …
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}
```

Remove the explicit "Back to main menu" option; `case` arms key on exact label
strings; `confirm_and_run`/`run_command` calls unchanged.

### Picker subsystem (`bin/lib/pickers.sh`)

- `parse_checker_names <text>` — from `check_health --list`: anchor on
  `Available checkers:`, emit first token of each subsequent indented line.
- `parse_pipeline_names <text>` — from `show_pipeline --all`: extract the quoted
  name from each `--- Pipeline: "…" ---` line (incl. inactive).
- `pick_or_cancel <title> <opt…>` — wraps `tuin_choose` with a prepended
  `← Cancel` entry; prints the chosen value to stdout, returns non-zero on
  Cancel/Ctrl-C/empty.
- Picker flows: run the list command in `tuin_spin "Loading…" -- …`, capture +
  parse; on empty/failure print a message and return; on selection feed the
  exact name to `confirm_and_run`.

### Per-submenu specifics

- **health.sh** — "Run a single checker" → checker picker; "Run specific
  checkers" stays free-text; rest mechanical.
- **pipeline.sh** — "Run by definition" + "Show one definition" → pipeline
  picker; "Show one run" (run-id) stays free-text; rest mechanical.
- **intelligence.sh** — verbose help block → `tuin_section` + concise lines;
  `custom_recommendations` → `tuin_confirm`/`tuin_input` chain (defaults: memory=y,
  disk=y, json=n, top-n=10, threshold=100).
- **notifications.sh** — nested menus → `tuin_menu`; driver name → static
  `pick_or_cancel "Driver" email slack pagerduty generic`; channel/message stay
  optional `tuin_input`.
- **cluster.sh** — mechanical; "specific checkers" (comma-separated) stays
  free-text.

## Error handling & non-TTY

- Picker cancel / Ctrl-C / EOF → clean return to submenu under `set -e`.
- Non-TTY/CI: menus/choosers read 1-indexed stdin line; confirm/input honor
  defaults — existing piped + bats callers keep working.
- Secrets discipline unchanged — pickers handle names only.

## Testing & verification

- Keep existing `bin/tests/test_cli.bats` (syntax + `--help`; no menu-text
  assertions, so no breakage).
- **New `bin/tests/test_pickers.bats`** — fixture strings (incl. Django
  system-check noise prefix and inactive pipelines) → assert
  `parse_checker_names`/`parse_pipeline_names` output; assert `pick_or_cancel`
  non-TTY selection value and that choosing `← Cancel` returns non-zero.
- `shellcheck bin/cli.sh bin/cli/*.sh bin/lib/tuin_vendor.sh bin/lib/pickers.sh`
  (exclude vendored `tuin.sh`).
- Manual smoke: `bash bin/cli.sh` — arrow menus, Back/Exit, pickers, cancel,
  confirm, free-text, non-TTY pipe.

## Docs to update

- `bin/README.md` (menu UI description), `bin/AGENTS.md` "Key modules" (note tuin
  + pickers), and any `docs/` page describing the CLI UX.

## Phased rollout (gradual — each phase leaves the CLI fully working)

Mixed state is safe: a submenu still on `select` returns into the tuin main loop
exactly as before, so phases can land independently.

1. **Phase 0 — Vendor (inert).** Commit `tuin.sh` + `tuin_vendor.sh`; wire
   `ensure_tuin` into `install.sh`. No UI change. Verify: file present,
   `tuin_version` runs, install self-heal works, bats green.
2. **Phase 1 — Core + main menu.** Source tuin in `cli.sh`; convert
   `show_banner`/`confirm_and_run`/`run_command` + `main_menu_loop` + `main()`.
   Submenus still `select`. Verify: main menu + jump commands work, Exit quits,
   bats green, shellcheck clean.
3. **Phase 2 — Submenus, one at a time.** Convert `health` → `pipeline` →
   `intelligence` → `notifications` → `cluster` mechanically (no pickers yet).
   Verify each independently before moving on.
4. **Phase 3 — Picker subsystem.** Add `lib/pickers.sh` + `test_pickers.bats`;
   wire checker picker into health, pipeline picker into pipeline, driver picker
   into notify. Verify: pickers, `← Cancel`, empty-list/failure handling.
5. **Phase 4 — Docs + final pass.** Update docs; full shellcheck + bats +
   interactive smoke.

## Acceptance criteria

- Vendored tuin committed & sourced; `ensure_tuin` in install.
- All five submenus + main menu converted; pickers working with `← Cancel` and
  empty-list handling.
- Untouched files unchanged; bats green (incl. new picker tests); shellcheck
  clean (excluding vendored `tuin.sh`); interactive smoke passes.