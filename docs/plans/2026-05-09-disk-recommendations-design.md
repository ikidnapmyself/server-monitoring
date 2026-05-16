---
title: "2026-05-09 Disk Cleanup Recommendations: Richer Format + More Rules Design"
parent: Plans
---

# Disk Cleanup Recommendations: Richer Format + More Rules Design

## Problem

The disk cleanup recommendations produced by `disk_common` / `disk_macos` / `disk_linux` are single-line nudges. Each rule is `(keywords, advice_string)`, where `advice_string` is one terse sentence — usually a fragment like *"Run 'pip cache purge' to clear pip cache"*. Two limitations follow:

1. **Coverage gaps.** Real space hogs surfaced by the live `disk_macos` run included `~/Library/Caches/JetBrains` (3.9 GB), `~/Library/Caches/composer` (3.3 GB), Logic Pro samples, etc. Today's rules cover pip / npm / Homebrew / Xcode / apt / journal / docker / snap and not much else.
2. **No instructions.** Even when a rule fires (e.g., *"Clear application caches in ~/Library/Caches"*), the operator gets a pointer but no actionable command. Compound advice ("stop the daemon first, then delete the cache") doesn't fit a one-liner.

The user wants richer per-rule content (multi-step instructions) **and** broader coverage (yarn, pnpm, JetBrains, composer, gradle, cargo, etc.).

## Scope

In scope:
- Change the rule shape from `tuple[list[str], str]` to `tuple[list[str], list[str]]` so each rule's advice is a list of lines: `[title, *details]`.
- Change the `recommendations` metric shape from `list[str]` to `list[list[str]]`. Each entry is a list of lines (1-element for single-line, N-element for richer rules).
- Extract per-tool rule constants into a new `apps/checkers/checkers/disk/recommendations.py` module so cross-platform tools (yarn, composer, JetBrains, etc.) can be referenced from multiple subclasses without duplicating strings.
- Update the three subclasses (`disk/common.py`, `disk/macos.py`, `disk/linux.py`) to import and reference the constants in their `recommendation_rules` lists.
- Update `BaseDiskAnalyzer._build_recommendations` to return `list[list[str]]`.
- Update `apps/checkers/management/commands/_metrics_format.py:write_metrics` to render each recommendation as `- {title}` followed by indented continuation lines.
- Update tests that assert on the old shape (mechanical, ~5–8 assertions).
- Add new tests: a unit-tests file for `disk/recommendations.py`; new cases in `test_base.py` and `test_metrics_format.py` for the multi-line shape.

Out of scope:
- Conditional advice (e.g., "only recommend `pip cache purge` if pip is on PATH"). The match is still substring-on-paths, same as today.
- Auto-running cleanup commands.
- Per-rule danger/safety levels or doc-URL metadata. The current design uses plain `list[str]`; a future PR could move to a dataclass if filtering or grouping by metadata becomes useful.
- Localization of advice strings.

This PR is **stacked on PR #137** (`fix/disk-checker-consolidation`). The shape change touches `BaseDiskAnalyzer._build_recommendations`, which only exists after #137 lands. Branched from `fix/disk-checker-consolidation`. After #137 merges, retarget this PR to `main`.

## Approach

### Rule shape

Before:
```python
recommendation_rules: list[tuple[list[str], str]] = [
    (["pip"], "Run 'pip cache purge' to clear pip cache"),
]
```

After:
```python
recommendation_rules: list[tuple[list[str], list[str]]] = [
    (["pip"], ["Clear pip cache:", "pip cache purge"]),
]
```

Each rule's second element is a list of lines: the first is the title (rendered as a bullet), subsequent lines are details (rendered indented under it). Single-step rules use a 1-element list.

### Metrics shape

Before: `metrics["recommendations"]` is `list[str]`.
After: `metrics["recommendations"]` is `list[list[str]]`. Each entry is `[title, *details]`. Single-string entries become 1-element lists.

This is a **breaking metrics shape change**, but there are no JSON consumers yet (`run_check --json` outputs the dict but nothing downstream parses it programmatically), so we land it cleanly in this PR rather than maintaining two formats.

### `BaseDiskAnalyzer._build_recommendations`

```python
def _build_recommendations(self, space_hogs, old_files, large_files) -> list[list[str]]:
    recs: list[list[str]] = []
    paths = [h["path"] for h in space_hogs]
    for keywords, lines in self.recommendation_rules:
        if not lines:
            continue
        if any(kw in p for kw in keywords for p in paths):
            recs.append(list(lines))
    if old_files and self.old_files_advice:
        recs.append([self.old_files_advice])
    if large_files and self.large_files_advice:
        recs.append([self.large_files_advice])
    return recs
```

Empty `lines` lists are silently skipped so a malformed rule doesn't render as `- ` (empty bullet). `old_files_advice` and `large_files_advice` stay as `str` on the subclass; `_build_recommendations` wraps them as 1-element lists at append time.

### `_metrics_format.write_metrics`

```python
recs = metrics.get("recommendations")
if recs:
    stdout.write(f"{indent}Recommendations:")
    for rec in recs:
        if not rec:
            continue
        stdout.write(f"{indent}  - {rec[0]}")
        for line in rec[1:]:
            stdout.write(f"{indent}    {line}")
```

Each recommendation arrives as an explicit list. First element renders with `- ` prefix; subsequent elements get a 4-space indent under the bullet.

Output example:
```
       Recommendations:
         - Clear pip cache:
             pip cache purge
         - Invalidate JetBrains IDE caches:
             In the IDE: File → Invalidate Caches and Restart
             Or delete ~/Library/Caches/JetBrains/<product> for products you no longer use
```

### Shared rules module — `apps/checkers/checkers/disk/recommendations.py`

```python
"""Shared cleanup-advice rules referenced by disk analyzers."""

# Each rule is (path-keywords, [title, *detail_lines]).
# Subclasses include only the rules whose keywords might appear in
# their declared scan_targets.

PIP = (["pip"], ["Clear pip cache:", "pip cache purge"])
NPM = (["npm", ".npm"], ["Clear npm cache:", "npm cache clean --force"])
YARN = (["yarn", "Yarn"], ["Clear Yarn cache:", "yarn cache clean"])
PNPM = (["pnpm"], ["Prune pnpm content-addressable store:", "pnpm store prune"])
COMPOSER = (["composer"], ["Clear Composer cache:", "composer clear-cache"])
GRADLE = (["gradle", ".gradle"], [
    "Clear old Gradle caches:",
    "Stop the Gradle daemon: gradle --stop",
    "Then delete ~/.gradle/caches/<old version>",
])
MAVEN = (["maven", ".m2"], [
    "Clear Maven local repository (large; consider keeping recent artifacts):",
    "rm -rf ~/.m2/repository (will re-download on next build)",
])
CARGO = (["cargo", ".cargo"], [
    "Clean Rust cargo cache:",
    "cargo install cargo-cache && cargo cache --autoclean",
])
GO_MODULES = (["go/pkg", "GOPATH"], [
    "Clean Go module cache:",
    "go clean -modcache",
])
JETBRAINS = (["JetBrains"], [
    "Invalidate JetBrains IDE caches:",
    "In the IDE: File → Invalidate Caches and Restart",
    "Or delete ~/Library/Caches/JetBrains/<product> for products you no longer use",
])
HOMEBREW = (["Homebrew"], [
    "Free Homebrew cache:",
    "brew cleanup --prune=all",
])
XCODE = (["DerivedData", "Xcode"], [
    "Remove Xcode DerivedData (safe; rebuilt on next build):",
    "rm -rf ~/Library/Developer/Xcode/DerivedData",
])
APPLE_CACHES = (["Caches"], [
    "Clear application caches under ~/Library/Caches (review per-app first)",
])
APT = (["apt"], ["Clear APT package cache:", "sudo apt clean"])
JOURNAL = (["journal"], [
    "Trim systemd journal logs:",
    "sudo journalctl --vacuum-size=100M",
])
DOCKER = (["docker"], [
    "Clean unused Docker data (containers, images, networks, build cache):",
    "docker system prune",
    "Add --volumes to also remove unused volumes (destructive)",
])
SNAP = (["snap"], [
    "Remove old snap package revisions:",
    "snap list --all | awk '/disabled/{print $1, $3}' | "
    "xargs -L 1 sudo snap remove --revision",
])
LOG_ROTATE = (["/var/log"], [
    "Compress or rotate large logs in /var/log",
    "Most distributions handle this with logrotate; check /etc/logrotate.d",
])
USER_CACHE = ([".cache"], [
    "Clear user caches in ~/.cache (review per-app first)",
])
```

### Subclass updates

`disk/common.py`:
```python
from apps.checkers.checkers.disk.recommendations import (
    LOG_ROTATE, PIP, NPM, YARN, PNPM, COMPOSER, GRADLE, MAVEN, CARGO,
    GO_MODULES, USER_CACHE,
)

class DiskCommonChecker(BaseDiskAnalyzer):
    ...
    recommendation_rules = [
        LOG_ROTATE, PIP, NPM, YARN, PNPM, COMPOSER,
        GRADLE, MAVEN, CARGO, GO_MODULES, USER_CACHE,
    ]
```

`disk/macos.py`:
```python
from apps.checkers.checkers.disk.recommendations import (
    HOMEBREW, XCODE, APPLE_CACHES, JETBRAINS, COMPOSER, YARN,
    PNPM, GRADLE, MAVEN, CARGO,
)

class DiskMacOSChecker(BaseDiskAnalyzer):
    ...
    recommendation_rules = [
        HOMEBREW, XCODE, APPLE_CACHES, JETBRAINS,
        COMPOSER, YARN, PNPM, GRADLE, MAVEN, CARGO,
    ]
```

`disk/linux.py`:
```python
from apps.checkers.checkers.disk.recommendations import (
    APT, JOURNAL, DOCKER, SNAP, JETBRAINS,
)

class DiskLinuxChecker(BaseDiskAnalyzer):
    ...
    recommendation_rules = [
        APT, JOURNAL, DOCKER, SNAP, JETBRAINS,
    ]
```

`old_files_advice` and `large_files_advice` stay as strings (single-line). The wrapping into a 1-element list happens in `_build_recommendations`.

## Edge cases

- **Empty `lines` list in a rule** (`(["foo"], [])`). `_build_recommendations` skips it — no empty bullet renders.
- **Empty `recommendations` list** (no rules matched, no advice strings). `write_metrics` falls through the `if recs:` guard — no `Recommendations:` header prints. Same as today.
- **Empty entry inside `recommendations`** (`recs = [["Title"], []]`). The empty inner list is skipped by the `if not rec: continue` guard in `write_metrics`.
- **Rule keywords overlap** (e.g., `YARN` keyword `"yarn"` and a hypothetical `YARN_BERRY` keyword `"yarn/berry"`). Both fire; both advice blocks appear. Acceptable.
- **`old_files_advice = ""`** default. `if X and self.advice:` short-circuits — no recommendation appended. Same as today.
- **JSON output via `run_check --json`**. `recommendations` becomes `list[list[str]]`. `json.dumps()` handles it natively. No JSON consumers exist yet, so the shape change lands cleanly without compatibility shims.
- **CLI output for non-disk checkers**. `recommendations` is a disk-checker-specific metric; cpu/memory/network/process don't emit it. `write_metrics`'s `if recs:` guard handles missing keys.
- **Long detail lines** (e.g., the SNAP one-liner). black keeps them on one line under 100 chars or wraps with implicit string concatenation. The output renders the long line as one detail (no soft-wrap). Acceptable; operators can copy-paste.

## Testing

### New unit tests — `apps/checkers/_tests/checkers/disk/test_recommendations.py`

1. `test_each_rule_has_keywords_and_lines` — iterate every public constant in the module; assert `isinstance(rule[0], list)` and `isinstance(rule[1], list)`, and both non-empty.
2. `test_rule_titles_are_distinct` — collect `rule[1][0]` (title) for every constant; assert no duplicates. Catches accidental edit-time copy-paste.
3. `test_known_keyword_substrings_match` — for canonical paths (`~/Library/Caches/JetBrains/foo`, `~/.cache/pip/x`, `/var/log/journal/y`), assert the right rule's keywords would substring-match.

### Updated tests — `test_base.py`

- Update existing recommendation tests to assert on lists, not strings: e.g., `assertIn(["matched advice"], result.metrics["recommendations"])` instead of `assertIn("matched advice", result.metrics["recommendations"])`. The stub subclass's `recommendation_rules` and advice strings are updated to the new shape.
- Add `test_multi_line_rule_returns_list_of_lines` — stub rule with 3-element lines list; assert the matched recommendation is the same 3-element list.
- Add `test_single_line_rule_returns_one_element_list` — stub rule with 1-element lines list; assert the matched recommendation is a 1-element list with that string.
- Add `test_empty_lines_rule_silently_dropped` — stub rule with `lines = []`; assert no recommendation entry is appended.

### Updated tests — `test_metrics_format.py`

- Update `test_recommendations` to use the new shape: `metrics={"recommendations": [["clean /tmp"]]}` and assert the bullet renders as `- clean /tmp` with no indented continuation.
- Add `test_recommendation_with_multiline_renders_indented` — pass `metrics={"recommendations": [["Title:", "step one", "step two"]]}`; assert output contains `- Title:` followed by `    step one` and `    step two` (4-space indent under the bullet).
- Add `test_empty_recommendation_skipped` — pass `metrics={"recommendations": [[], ["Real title"]]}`; assert only the real one renders.

### Updated tests — cleanup-checker tests

Existing `test_common.py` / `test_macos.py` / `test_linux.py` tests that assert on specific recommendation strings (e.g., `assertIn("Clear pip cache", recs)`) now need to flatten before substring-checking, e.g.:
```python
flat = [line for rec in recs for line in rec]
self.assertIn("pip cache purge", flat)
```

Or assert on the title line directly: `assertIn(["Clear pip cache:", "pip cache purge"], recs)`.

I'll favor the title-line check where the test is exercising a specific rule (more precise and fails informatively if the title changes). Where the test is just confirming "some pip-related advice fires," the flat-substring check is fine.

### Coverage

Coverage stays at 100% on `disk/base.py`, `disk/recommendations.py`, and `_metrics_format.py`. The new `if not lines: continue` and `if not rec: continue` branches are exercised by the new "empty-rule" / "empty-entry" tests.

## Notes for implementation

- **Single PR with one or two commits.** A clean split would be:
  1. **Format change:** update `_build_recommendations` and `write_metrics` to the new shape, update the three subclasses' `recommendation_rules` to use the new tuple shape (still inline), update existing tests to the new assertion shape. No new content, just shape change. All existing tests pass.
  2. **Extract + extend:** move rules into `disk/recommendations.py`, add the new rules (yarn, pnpm, JetBrains, composer, gradle, maven, cargo, go modules, etc.), update subclasses to import constants. Add new tests for the constants module.

  Single commit is also acceptable — the shape change and the new content are tightly coupled.
- **No CLI flags, no behavior toggles, no thresholds.**
- **`recommendations` shape change is breaking** but has no consumers; document it clearly in the PR body.
- **Stacked on #137.** Branch base will need to be retargeted from `fix/disk-checker-consolidation` to `main` after #137 merges.
- **Don't add a danger/safety metadata field.** The design considered it (Approach C in brainstorming) and explicitly deferred it. If/when filtering or color-coding becomes useful, that's a separate small PR.