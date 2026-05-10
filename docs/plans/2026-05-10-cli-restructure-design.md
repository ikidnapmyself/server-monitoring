---
title: "Interactive CLI Restructure Design"
parent: Plans
---

# Interactive CLI Restructure Design

## Problem

`bin/cli.sh` and its menu modules (`bin/cli/*.sh`) drifted out of alignment with the actual Django management commands. A full audit found:

- **4 broken invocations** that fail at runtime (argparse rejects them):
  - `bin/cli/alerts.sh:49` — `run_check --list` (no such flag; `run_check` requires a `checker` positional).
  - `bin/cli/pipeline.sh:98` — `run_pipeline --list` (no such flag; the listing command is `show_pipeline --all`).
  - `bin/cli/pipeline.sh:103,111` — `run_pipeline <name>` (no positional; needs `--definition <name>`).
  - `bin/cli/pipeline.sh:150,155,163` — `monitor_pipeline --list`, `<id>`, `--follow` (none of these flags exist; correct flags are `--limit`/`--status`/`--run-id`).
- **4 misleading sections**:
  - "Alerts & Incidents" — entirely misnamed; contains `run_check` (a checker) and `run_pipeline --checks-only` (orchestration). Nothing in this menu touches the `alerts` Django app or Alert/Incident models.
  - "Run Checks Pipeline" submenu — help blurb mentions `--no-incidents` and `--hostname` but never offers them as choices.
  - "Health & Monitoring" — `preflight` (a `checkers`-app command) lives in System & Security instead of here.
  - "System & Security" — only contains `preflight` and a shell script. The "Security" half oversells the contents.
- **2 commands not exposed anywhere**:
  - `alerts:push_to_hub` — runtime cluster-mode push.
  - `orchestration:setup_instance` — interactive wizard for `PipelineDefinition` / `NotificationChannel` / `IntelligenceProvider`.

The user reported the issue after hitting the broken `run_check --list` and noting that "Alerts & Incidents makes no sense."

## Scope

In scope:
- Rewrite the top-level menu to **7 menus + Exit**, eliminating the misleading "Alerts & Incidents" and "System & Security" menus and adding a new **Cluster** menu for `push_to_hub`.
- Fix all 4 broken invocations.
- Move `run_check` from the deleted Alerts menu into Health.
- Move `run_pipeline --checks-only` from the deleted Alerts menu into Pipeline.
- Move `preflight` from the deleted System menu into Health.
- Move `set_production.sh` from the deleted System menu into Install.
- Add `setup_instance` (Django wizard) into Install.
- Create `bin/cli/cluster.sh` with 3 `push_to_hub` items.
- Delete `bin/cli/alerts.sh` and `bin/cli/system.sh`.
- Drop the `cli.sh alerts` and `cli.sh system` jump commands; add `cli.sh cluster`.
- Update `bin/tests/test_cli.bats` to reflect the new menu set (smoke tests only — the existing tests verify syntax + help output, not menu invocations).

Out of scope:
- Surfacing every underexposed flag (`run_pipeline --notify-driver`, `--label`, `--trace-id`; `check_health --warning-threshold`; `test_notify --severity`; per-driver `test_notify` flags; `monitor_pipeline --status` / `--limit`; etc.). The audit listed many. Adding them all would balloon the menus; power users invoke `--help` directly. A future "Custom flags per command" feature is a separate design.
- The Intelligence menu's surprising `--path=$PROJECT_DIR` default for disk recommendations (the underlying `get_recommendations` defaults to `/`). Noted but not changed in this PR.
- Renaming any other menu (Pipeline → Orchestration, etc.). Only the two misleading menus disappear.
- Localization, color theming, or accessibility changes.
- Any change to the underlying Django commands. CLI-only restructure.

## Approach — Approach C (full audit + restructure)

Approaches considered (from brainstorming):
- **A**: surgical fix — drop the broken `run_check --list` only. Lowest risk; doesn't address the conceptual debt.
- **B**: targeted restructure — delete Alerts menu, redistribute its items. Keep System menu.
- **C** (chosen): full audit. Verify every invocation; reorganize menus to match real domains; expose missing commands.

C wins because the audit found broken invocations in three different menus (alerts and pipeline both have multiple), and the conceptual debt is in two menus, not just one.

### New top-level layout

| # | Menu | Source(s) | Notes |
|---|---|---|---|
| 1 | Install / Setup | existing + `setup_instance` (NEW) + `set_production.sh` (moved from System) | extended |
| 2 | Health | `check_health` + `run_check` (moved from Alerts) + `preflight` (moved from System) | extended |
| 3 | Pipeline | `run_pipeline` (fixed) + `show_pipeline` + `monitor_pipeline` (fixed) + `--checks-only` (moved from Alerts) | fixed + extended |
| 4 | Intelligence | unchanged | unchanged |
| 5 | Notifications | unchanged | unchanged |
| 6 | **Cluster** (new) | `push_to_hub` (newly exposed) | NEW |
| 7 | Updates | unchanged | unchanged |
| 8 | Exit | unchanged | unchanged |

### Health menu (new flat layout, 8 items + Back)

```
═══ Health ═══
1. Run all health checks                       → check_health
2. Run specific checkers                       → check_health <names>  (lists first, then prompts)
3. Run a single checker                        → run_check <name>
4. List available checkers                     → check_health --list
5. Preflight dashboard                         → preflight
6. JSON output (all checks)                    → check_health --json
7. CI mode: fail on warning                    → check_health --fail-on-warning
8. CI mode: fail on critical only              → check_health --fail-on-critical
9. Back to main menu
```

Item 4 (`check_health --list`) replaces the broken `run_check --list` from the deleted Alerts menu.

### Pipeline menu (new flat layout, 9 items + Back)

```
═══ Pipeline ═══
1. Run pipeline (sample payload)               → run_pipeline --sample
2. Run pipeline by definition                  → run_pipeline --definition <name>   (FIXED: was bare positional)
3. Run pipeline from file                      → run_pipeline --file <path>
4. Run checks only (orchestrated)              → run_pipeline --checks-only         (moved from Alerts)
5. Run checks only (dry run)                   → run_pipeline --checks-only --dry-run
6. List pipeline definitions                   → show_pipeline --all                (FIXED: was run_pipeline --list)
7. Show one pipeline definition                → show_pipeline --name <name>
8. List recent pipeline runs                   → monitor_pipeline                   (FIXED: default lists)
9. Show one pipeline run                       → monitor_pipeline --run-id <id>     (FIXED: was bare positional)
10. Back to main menu
```

Submenus collapse — they obscured more than they organized. The non-existent `monitor_pipeline --follow` is dropped.

### Cluster menu (new module — `bin/cli/cluster.sh`)

```
═══ Cluster ═══
1. Push checks to hub                          → push_to_hub
2. Push checks to hub (dry run)                → push_to_hub --dry-run
3. Push checks to hub (specific checkers)      → push_to_hub --checkers a,b,c   (comma-separated)
4. Back to main menu
```

`push_to_hub --json` is intentionally not exposed — operators driving the interactive CLI rarely need raw JSON for a single push. Power users invoke `--help`.

### Install menu (extended)

Add 2 items before "Back to main menu":

```
11. Set up monitoring instance (wizard)        → setup_instance
12. Set production mode                        → set_production.sh
```

`setup_instance` is interactive (Django wizard creating `PipelineDefinition` / `NotificationChannel` / `IntelligenceProvider` records). Belongs in setup work, not in Pipeline runtime ops.

`set_production.sh` is a one-shot config flip — fits the install/setup theme and removes the need for a 1-item "System" menu.

### `bin/cli.sh` changes

- Drop the `alerts` and `system` cases from the `main()` dispatch (lines 173-178 and 203-208).
- Drop `alerts` and `system` from the `show_help()` listing (lines 57 and 62).
- Add `cluster` jump command pointing at `cluster_menu`; document in both the comment header and `show_help()`.
- Update `show_main_menu()`'s `options` array (lines 122-132) to the new 7-item layout (Cluster replaces Alerts; System removed; numbering closes the gap).
- Update the case dispatch (lines 134-148) accordingly.
- Source `bin/cli/cluster.sh` instead of `bin/cli/alerts.sh` (line 107).
- Stop sourcing `bin/cli/system.sh` (line 112).

### File changes summary

| Path | Action |
|---|---|
| `bin/cli/cluster.sh` | **CREATE** (new module hosting `cluster_menu`) |
| `bin/cli/health.sh` | **REWRITE** (8 items + Back, adds `run_check` and `preflight`) |
| `bin/cli/pipeline.sh` | **REWRITE** (flat 9 items + Back, fixes 4 broken invocations, absorbs `--checks-only`) |
| `bin/cli/install_menu.sh` | **EXTEND** (adds 2 items before "Back") |
| `bin/cli.sh` | **MODIFY** (main menu options, case dispatch, sourcing, jump commands, help) |
| `bin/cli/alerts.sh` | **DELETE** |
| `bin/cli/system.sh` | **DELETE** |
| `bin/tests/test_cli.bats` | **EXTEND** (smoke tests for the new menu set; verify deleted jump commands now error) |

## Edge cases

- **No deprecation shim for `cli.sh alerts` / `cli.sh system`** — these jump commands were misleading from day one. They'll print "Unknown command" via the existing `*)` branch in `main()`. Acceptable; clean removal beats a shim.
- **Sourcing order matters** — `bin/cli.sh:105-112` sources menu modules in a fixed order. New layout: install_menu, health, cluster (replaces alerts), intelligence, pipeline, notifications, update. (system dropped.)
- **`SCRIPT_DIR` and `confirm_and_run`** — all menus rely on globals defined in `bin/cli.sh`. The new `cluster.sh` follows the same convention. No new helpers introduced.
- **`set_production.sh` is a shell script, not a Django command** — wrapped via `confirm_and_run "$SCRIPT_DIR/set_production.sh"`, same as today's call from `system.sh:21`.
- **Existing BATS tests are smoke-only** — `bin/tests/test_cli.bats` has 4 tests (syntax check on `cli.sh`, `--help` shows usage, unknown command exits 1, all menu modules pass syntax check). New tests will verify: the new `cli.sh cluster` jump works (dispatches without erroring out before the menu prompt); `cli.sh alerts` and `cli.sh system` no longer dispatch (print "Unknown command" + exit 1).
- **`bin/cli/alerts.sh` deletion vs git history** — `git mv` not appropriate (the file's contents redistribute across multiple files, not move to one). Use `git rm` and `git add` for the new files. Reviewers can trace specific items via the design doc table above.
- **No metrics shape changes, no Django command changes** — purely shell-script restructure.

## Testing

### Updated `bin/tests/test_cli.bats`

Existing 4 tests stay (syntax + help + unknown-command + module-syntax). Add:

- `cli.sh --help no longer mentions 'alerts' or 'system' jump commands`
- `cli.sh --help mentions 'cluster' jump command`
- `cli.sh alerts exits 1 with Unknown command` (deleted jump command)
- `cli.sh system exits 1 with Unknown command` (deleted jump command)
- `bin/cli/cluster.sh passes syntax check` (covered by the existing "all cli modules" test once the module exists)
- `bin/cli/alerts.sh and bin/cli/system.sh do not exist` (lock the deletion)

### Manual sanity check on this Mac

After the rewrite, run interactively:
- `bin/cli.sh` → main menu → each of the 7 numbered options → confirm the menu opens without errors
- `bin/cli.sh cluster` → confirm the new jump works
- `bin/cli.sh alerts` and `bin/cli.sh system` → confirm both error out with "Unknown command" + exit 1
- Pick "Pipeline" → "List pipeline definitions" → confirm it actually runs `show_pipeline --all` and lists entries (or emits a clean "no definitions yet" message)
- Pick "Pipeline" → "List recent pipeline runs" → confirm `monitor_pipeline` runs without `--list`
- Pick "Health" → "Run a single checker" → enter "cpu" → confirm it dispatches `run_check cpu` correctly

### Coverage

`bin/cli.sh` and `bin/cli/*.sh` aren't covered by Python coverage; their tests are BATS only. The CI's "Shell Tests (BATS)" check will validate syntax and help output on every PR.

## Notes for implementation

- **Single PR with one logical commit**, or two-commit split:
  1. **Delete + restructure** — delete `alerts.sh` and `system.sh`, rewrite `health.sh` and `pipeline.sh`, modify `cli.sh`, extend `install_menu.sh`, add new BATS tests for jump commands.
  2. **Add Cluster menu** — create `bin/cli/cluster.sh`, source it in `cli.sh`, add `cluster` jump command and option 6 in main menu.

  Either is acceptable. A single commit is fine because the restructure has internal consistency (the menu can't render correctly mid-way through the change).
- **`set -e` is set at the top of `cli.sh`** — sourced modules inherit this. Don't introduce error-suppressing patterns in the new module.
- **Menu numbering** is via `select`'s `$REPLY` global — straightforward 1-N indices. The "Back" item is always last.
- **No changes to `bin/install.sh`, `bin/update.sh`, `bin/set_production.sh`, or any non-CLI shell code.**
- **No changes to any Python code.** Pure shell-script work.
- **Don't reorder existing items within Install / Notifications / Intelligence / Updates** — only add to Install (2 new items at positions 11-12); the others stay byte-identical.