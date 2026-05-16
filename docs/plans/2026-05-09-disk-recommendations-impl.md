---
title: "2026-05-09 Disk Cleanup Recommendations Implementation Plan"
parent: Plans
---

# Disk Cleanup Recommendations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert disk-cleanup recommendations from single-line strings to multi-line `[title, *details]` lists, and extend coverage with ~10 new tool-specific rules (yarn, pnpm, JetBrains, composer, gradle, maven, cargo, go modules, etc.) extracted into a shared `disk/recommendations.py` constants module.

**Architecture:** Two-commit split. **Commit A** is the format change: `BaseDiskAnalyzer._build_recommendations` returns `list[list[str]]` instead of `list[str]`; `_metrics_format.write_metrics` renders each entry's first element as a bullet and subsequent elements as 4-space indented continuation lines; the three subclasses' inline `recommendation_rules` tuples convert from `(keywords, str)` to `(keywords, list[str])`. Existing tests' assertions update mechanically. **Commit B** extracts rules into `apps/checkers/checkers/disk/recommendations.py` as named constants, adds ~10 new tool-specific rules, and updates the three subclasses to import and reference the constants.

**Tech Stack:** Python 3, Django management commands, `unittest.TestCase`, pytest, coverage.

**Design doc:** `docs/plans/2026-05-09-disk-recommendations-design.md`

**Branch:** `feat/disk-recommendations` (already created from `fix/disk-checker-consolidation`'s HEAD; design doc committed at `f459db3`).

**Stacked on PR #137.** Base for the eventual GitHub PR is `fix/disk-checker-consolidation` until #137 merges, then retarget to `main`.

---

## Background — what's changing

Today's rule shape (post #137):
```python
recommendation_rules: list[tuple[list[str], str]] = [
    (["pip"], "Run 'pip cache purge' to clear pip cache"),
]
```

After this PR:
```python
recommendation_rules: list[tuple[list[str], list[str]]] = [
    (["pip"], ["Clear pip cache:", "pip cache purge"]),
]
```

`metrics["recommendations"]` shape changes from `list[str]` to `list[list[str]]`. JSON output (`run_check --json`) emits the new structured shape. No JSON consumers exist today, so no compatibility shim.

CLI output:
```
       Recommendations:
         - Clear pip cache:
             pip cache purge
         - Invalidate JetBrains IDE caches:
             In the IDE: File → Invalidate Caches and Restart
             Or delete ~/Library/Caches/JetBrains/<product> for products you no longer use
```

---

## Commit A — Format change (no new content)

### Task 1: Update `BaseDiskAnalyzer._build_recommendations` and the three subclasses

**Files to modify:**
- `apps/checkers/checkers/disk/base.py` — `_build_recommendations` return type and body.
- `apps/checkers/checkers/disk/common.py` — `recommendation_rules` tuple shape.
- `apps/checkers/checkers/disk/macos.py` — `recommendation_rules` tuple shape.
- `apps/checkers/checkers/disk/linux.py` — `recommendation_rules` tuple shape.

**Step 1: Update `disk/base.py:_build_recommendations`**

Find the existing method and replace it with:

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

Also update the class-attribute typing:
```python
    recommendation_rules: list[tuple[list[str], list[str]]] = []
```

`old_files_advice` and `large_files_advice` stay as `str` on the class.

**Step 2: Update `disk/common.py` `recommendation_rules`**

Replace each rule tuple's second element from `str` to `list[str]`. Keep the same content for now; this commit doesn't add new rules.

```python
    recommendation_rules = [
        (["/var/log"], ["Compress or rotate old log files in /var/log"]),
        (["pip"], ["Run 'pip cache purge' to clear pip cache"]),
        (["npm", ".npm"], ["Run 'npm cache clean --force' to clear npm cache"]),
        ([".cache"], ["Clear user caches in ~/.cache"]),
    ]
```

(In Commit B these get replaced with imported constants and richer multi-line content.)

**Step 3: Update `disk/macos.py` `recommendation_rules`**

```python
    recommendation_rules = [
        (["Homebrew"], ["Run 'brew cleanup --prune=all' to free Homebrew cache"]),
        (
            ["DerivedData", "Xcode"],
            ["Remove ~/Library/Developer/Xcode/DerivedData to free build cache"],
        ),
        (["Caches"], ["Clear application caches in ~/Library/Caches"]),
    ]
```

**Step 4: Update `disk/linux.py` `recommendation_rules`**

```python
    recommendation_rules = [
        (["apt"], ["Run 'sudo apt clean' to clear APT package cache"]),
        (["journal"], ["Run 'sudo journalctl --vacuum-size=100M' to trim journal logs"]),
        (["docker"], ["Run 'docker system prune' to clean unused Docker data"]),
        (["snap"], ["Remove old snap package revisions"]),
    ]
```

**Step 5: Run the disk tests — expect failures**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/ -v 2>&1 | tail -30
```

Expected: many `test_*_recommendation` tests fail because today's assertions use `any("substring" in r for r in recs)` where `r` was a string and is now a list. They need updating in the next task. STOP if any UNEXPECTED test fails (unrelated to recommendations shape).

---

### Task 2: Update `_metrics_format.write_metrics` to render the new shape

**Files to modify:**
- `apps/checkers/management/commands/_metrics_format.py` — the `recs = metrics.get("recommendations")` block.

**Step 1: Replace the recommendations rendering block**

Find:
```python
    recs = metrics.get("recommendations")
    if recs:
        stdout.write(f"{indent}Recommendations:")
        for rec in recs:
            stdout.write(f"{indent}  - {rec}")
```

Replace with:
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

**Step 2: Run the metrics-format tests — expect one failure**

```bash
uv run pytest apps/checkers/_tests/test_metrics_format.py -v 2>&1 | tail -20
```

Expected: `test_recommendations` fails because it passes the OLD shape (`["clean /tmp"]`). Will fix in the next task. Other tests should still pass.

---

### Task 3: Update existing test assertions to the new shape

Mechanical pass through all tests that touch `recommendations`. The pattern is:

- `any("substring" in r for r in recs)` (where `r` was a string) → `any("substring" in line for r in recs for line in r)` (now `r` is a list of lines).
- `assertIn("matched advice", recs)` → `assertIn(["matched advice"], recs)`.
- `metrics={"recommendations": ["clean /tmp"]}` → `metrics={"recommendations": [["clean /tmp"]]}`.

**Files to modify:**
- `apps/checkers/_tests/checkers/disk/test_base.py`
- `apps/checkers/_tests/checkers/disk/test_common.py`
- `apps/checkers/_tests/checkers/disk/test_macos.py`
- `apps/checkers/_tests/checkers/disk/test_linux.py`
- `apps/checkers/_tests/test_metrics_format.py`

**Step 1: `test_base.py` updates**

The stub class `_StubAnalyzer` has `recommendation_rules = [(["match_keyword"], "matched advice")]`. Update to `[(["match_keyword"], ["matched advice"])]`.

Also: `old_files_advice = "old advice"` and `large_files_advice = "large advice"` stay as strings (the wrapping happens in `_build_recommendations`).

Update assertions:
- `self.assertIn("matched advice", result.metrics["recommendations"])` → `self.assertIn(["matched advice"], result.metrics["recommendations"])`
- `self.assertNotIn("matched advice", ...)` → `self.assertNotIn(["matched advice"], ...)`
- `self.assertIn("old advice", recs)` → `self.assertIn(["old advice"], recs)` (old_files_advice gets wrapped as 1-element list)
- `self.assertIn("large advice", recs)` → `self.assertIn(["large advice"], recs)`
- `self.assertNotIn("old advice", recs)` → `self.assertNotIn(["old advice"], recs)`
- `self.assertNotIn("large advice", recs)` → `self.assertNotIn(["large advice"], recs)`

**Step 2: `test_common.py`, `test_macos.py`, `test_linux.py` — update `_build_recommendations` test assertions**

Each file has a `*BuildRecommendationsTests` class with tests like:
```python
recs = checker._build_recommendations(space_hogs, [], [])
self.assertTrue(any("/var/log" in r for r in recs))
```

Update each such pattern to:
```python
recs = checker._build_recommendations(space_hogs, [], [])
self.assertTrue(any("/var/log" in line for r in recs for line in r))
```

There are roughly:
- `test_common.py`: 7 substring-style assertions in `DiskCommonBuildRecommendationsTests`
- `test_macos.py`: 6 in `DiskMacOSBuildRecommendationsTests`
- `test_linux.py`: 5 in `DiskLinuxBuildRecommendationsTests`

The `assertEqual(recs, [])` (no-matches-empty-recommendations) tests stay unchanged — empty list is empty list.

The `test_includes_recommendations` smoke tests use `assertIsInstance(result.metrics["recommendations"], list)` — that's still true (a `list[list[str]]` is still a `list`). No change needed there.

The `test_includes_linux_recommendations` test has:
```python
self.assertTrue(any("apt" in r.lower() for r in recs))
```
Update to:
```python
self.assertTrue(any("apt" in line.lower() for r in recs for line in r))
```

**Step 3: `test_metrics_format.py:test_recommendations` update**

Find:
```python
    def test_recommendations(self):
        output = self._render({"recommendations": ["clean /tmp"]})
        self.assertIn("Recommendations:", output)
        self.assertIn("- clean /tmp", output)
```

Replace with:
```python
    def test_recommendations(self):
        output = self._render({"recommendations": [["clean /tmp"]]})
        self.assertIn("Recommendations:", output)
        self.assertIn("- clean /tmp", output)
```

**Step 4: Run the full disk + metrics-format test suite**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/ apps/checkers/_tests/test_metrics_format.py -v 2>&1 | tail -10
```

Expected: all PASS.

If a test fails because of an assertion you missed, find it and update it. The pattern is uniform: `r` becomes `for line in r for r in recs`.

---

### Task 4: Add new tests for the multi-line shape

**Files to modify:**
- `apps/checkers/_tests/checkers/disk/test_base.py` — add 3 new tests.
- `apps/checkers/_tests/test_metrics_format.py` — add 2 new tests.

**Step 1: Add `test_base.py` tests**

Append to `BaseDiskAnalyzerTests` (or as a new class — use the existing one):

```python
    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_multi_line_rule_returns_list_of_lines(
        self, mock_scan, _old, _large
    ):
        """A rule with multiple lines yields one list-of-lines entry."""

        class _MultiLineStub(BaseDiskAnalyzer):
            name = "_multiline"
            scan_targets = ["/test"]
            old_file_targets = []
            large_file_targets = []
            recommendation_rules = [
                (["match"], ["Title:", "step one", "step two"]),
            ]

            def _is_applicable(self) -> bool:
                return True

        mock_scan.return_value = [{"path": "/test/match/x", "size_mb": 10.0}]
        result = _MultiLineStub().check()
        self.assertIn(["Title:", "step one", "step two"], result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_single_line_rule_returns_one_element_list(
        self, mock_scan, _old, _large
    ):
        """A rule with a single-line list yields a 1-element list."""

        class _SingleLineStub(BaseDiskAnalyzer):
            name = "_singleline"
            scan_targets = ["/test"]
            old_file_targets = []
            large_file_targets = []
            recommendation_rules = [(["match"], ["solo line"])]

            def _is_applicable(self) -> bool:
                return True

        mock_scan.return_value = [{"path": "/test/match/x", "size_mb": 10.0}]
        result = _SingleLineStub().check()
        self.assertIn(["solo line"], result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_empty_lines_rule_silently_dropped(self, mock_scan, _old, _large):
        """A rule with an empty lines list never produces a recommendation."""

        class _EmptyLinesStub(BaseDiskAnalyzer):
            name = "_emptylines"
            scan_targets = ["/test"]
            old_file_targets = []
            large_file_targets = []
            recommendation_rules = [(["match"], [])]

            def _is_applicable(self) -> bool:
                return True

        mock_scan.return_value = [{"path": "/test/match/x", "size_mb": 10.0}]
        result = _EmptyLinesStub().check()
        self.assertEqual(result.metrics["recommendations"], [])
```

**Step 2: Add `test_metrics_format.py` tests**

Append to `WriteMetricsTests`:

```python
    def test_recommendation_with_multiline_renders_indented(self):
        output = self._render(
            {"recommendations": [["Title:", "step one", "step two"]]}
        )
        self.assertIn("Recommendations:", output)
        self.assertIn("- Title:", output)
        self.assertIn("    step one", output)
        self.assertIn("    step two", output)

    def test_empty_recommendation_skipped(self):
        output = self._render({"recommendations": [[], ["Real title"]]})
        self.assertIn("- Real title", output)
        # Should not produce stray "- " bullets from the empty entry
        self.assertNotIn("- \n", output)
```

**Step 3: Run the new tests**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/test_base.py apps/checkers/_tests/test_metrics_format.py -v
```

Expected: all 5 new tests PASS.

---

### Task 5: Verify, lint, commit Commit A

**Step 1: Full suite**

```bash
uv run pytest apps/checkers/ -v 2>&1 | tail -10
```

Expected: all PASS.

**Step 2: Coverage**

```bash
uv run coverage run --branch -m pytest apps/checkers/_tests/checkers/disk/ apps/checkers/_tests/test_metrics_format.py
uv run coverage report -m --include='apps/checkers/checkers/disk/*,apps/checkers/management/commands/_metrics_format.py'
```

Expected: 100% on every file in `disk/` and on `_metrics_format.py`.

**Step 3: Lint, format, type-check**

```bash
uv run black --check apps/checkers/
uv run ruff check apps/checkers/
uv run mypy apps/checkers/checkers/ apps/checkers/management/commands/
```

Expected: clean.

**Step 4: Commit**

```bash
git add apps/checkers/
git commit -m "$(cat <<'EOF'
refactor(checkers): change recommendation_rules to list-of-lines shape

Each rule's advice becomes a list of lines [title, *details] instead
of a single string. metrics["recommendations"] becomes
list[list[str]]. write_metrics renders the first line as a bullet
and subsequent lines as 4-space-indented continuation under it.

Pure shape change — same recommendation content, no new rules. Tests
updated mechanically: any("X" in r for r in recs) becomes
any("X" in line for r in recs for line in r). Three new test_base
tests cover multi-line, single-line, and empty-lines rules; two new
test_metrics_format tests cover multi-line rendering and empty-entry
skipping.

JSON output (run_check --json) now emits list-of-lists for
recommendations. No JSON consumers exist yet, so no shim.

Follow-up commit will extract rule constants into a shared module
and add new tool-specific rules (yarn, JetBrains, composer, etc.).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass. After commit, `git status` clean.

---

## Commit B — Extract constants + add new rules

### Task 6: Create `disk/recommendations.py` with the rule constants

**Files to create:**
- `apps/checkers/checkers/disk/recommendations.py`

**Step 1: Write the module**

```python
"""Shared cleanup-advice rules referenced by disk analyzers.

Each rule is (path-keywords, [title, *detail_lines]). Subclasses
include only the rules whose keywords might appear in their declared
scan_targets.
"""

# Cross-platform development tooling
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

# macOS-specific
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

# Linux-specific
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

# Cross-platform system targets
LOG_ROTATE = (["/var/log"], [
    "Compress or rotate large logs in /var/log",
    "Most distributions handle this with logrotate; check /etc/logrotate.d",
])
USER_CACHE = ([".cache"], [
    "Clear user caches in ~/.cache (review per-app first)",
])
```

---

### Task 7: Add unit tests for the constants module

**Files to create:**
- `apps/checkers/_tests/checkers/disk/test_recommendations.py`

**Step 1: Write the test file**

```python
"""Unit tests for the shared recommendation constants."""

from django.test import SimpleTestCase

from apps.checkers.checkers.disk import recommendations


def _all_rules():
    """Yield every rule constant defined in the recommendations module."""
    for name in dir(recommendations):
        if name.startswith("_"):
            continue
        value = getattr(recommendations, name)
        if isinstance(value, tuple) and len(value) == 2:
            yield name, value


class RecommendationsModuleTests(SimpleTestCase):
    def test_each_rule_has_keywords_and_lines(self):
        for name, (keywords, lines) in _all_rules():
            with self.subTest(rule=name):
                self.assertIsInstance(keywords, list, f"{name}: keywords must be a list")
                self.assertGreater(len(keywords), 0, f"{name}: keywords must be non-empty")
                self.assertTrue(
                    all(isinstance(k, str) for k in keywords),
                    f"{name}: every keyword must be a string",
                )
                self.assertIsInstance(lines, list, f"{name}: lines must be a list")
                self.assertGreater(len(lines), 0, f"{name}: lines must be non-empty")
                self.assertTrue(
                    all(isinstance(line, str) for line in lines),
                    f"{name}: every line must be a string",
                )

    def test_rule_titles_are_distinct(self):
        """Catches accidental copy-paste during edits."""
        titles = [lines[0] for _name, (_keywords, lines) in _all_rules()]
        duplicates = [t for t in titles if titles.count(t) > 1]
        self.assertEqual(duplicates, [], f"Duplicate titles: {sorted(set(duplicates))}")

    def test_known_keyword_substrings_match(self):
        """Canonical paths trigger the right rule."""
        cases = [
            ("/Users/me/Library/Caches/JetBrains/PyCharm", recommendations.JETBRAINS),
            ("/Users/me/.cache/pip/wheels/abc", recommendations.PIP),
            ("/Users/me/.cache/yarn/v6/deadbeef", recommendations.YARN),
            ("/var/log/journal/abc", recommendations.JOURNAL),
            ("/var/cache/apt/archives", recommendations.APT),
            ("/var/lib/docker/overlay2", recommendations.DOCKER),
            ("/Users/me/Library/Caches/composer/repo", recommendations.COMPOSER),
        ]
        for path, (keywords, _lines) in cases:
            with self.subTest(path=path):
                self.assertTrue(
                    any(kw in path for kw in keywords),
                    f"None of {keywords} matched in {path}",
                )
```

**Step 2: Run the tests**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/test_recommendations.py -v
```

Expected: 3 PASS.

If `test_rule_titles_are_distinct` fails, two rules share a title — adjust one to be more specific.

---

### Task 8: Wire constants into the three subclasses

**Files to modify:**
- `apps/checkers/checkers/disk/common.py`
- `apps/checkers/checkers/disk/macos.py`
- `apps/checkers/checkers/disk/linux.py`

**Step 1: `disk/common.py`**

Add the import block near the top (after the existing `BaseDiskAnalyzer` import):

```python
from apps.checkers.checkers.disk.recommendations import (
    CARGO,
    COMPOSER,
    GO_MODULES,
    GRADLE,
    LOG_ROTATE,
    MAVEN,
    NPM,
    PIP,
    PNPM,
    USER_CACHE,
    YARN,
)
```

Replace the inline `recommendation_rules`:

```python
    recommendation_rules = [
        LOG_ROTATE,
        PIP,
        NPM,
        YARN,
        PNPM,
        COMPOSER,
        GRADLE,
        MAVEN,
        CARGO,
        GO_MODULES,
        USER_CACHE,
    ]
```

**Step 2: `disk/macos.py`**

Add import:

```python
from apps.checkers.checkers.disk.recommendations import (
    APPLE_CACHES,
    CARGO,
    COMPOSER,
    GRADLE,
    HOMEBREW,
    JETBRAINS,
    MAVEN,
    PNPM,
    XCODE,
    YARN,
)
```

Replace the inline `recommendation_rules`:

```python
    recommendation_rules = [
        HOMEBREW,
        XCODE,
        APPLE_CACHES,
        JETBRAINS,
        COMPOSER,
        YARN,
        PNPM,
        GRADLE,
        MAVEN,
        CARGO,
    ]
```

**Step 3: `disk/linux.py`**

Add import:

```python
from apps.checkers.checkers.disk.recommendations import (
    APT,
    DOCKER,
    JETBRAINS,
    JOURNAL,
    SNAP,
)
```

Replace the inline `recommendation_rules`:

```python
    recommendation_rules = [
        APT,
        JOURNAL,
        DOCKER,
        SNAP,
        JETBRAINS,
    ]
```

**Step 4: Run the disk test suite**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/ -v 2>&1 | tail -10
```

Expected: all PASS. The substring-checked assertions (`any("apt clean" in line for r in recs for line in r)`) still pass because the new constants preserve the exact strings the tests look for ("apt clean", "pip cache purge", etc.).

If a test fails because a substring it expected no longer appears, the rule's content drifted in a meaningful way. Read the failure carefully — does the new content cover the same idea with different wording? If yes, update the test's substring. If no, fix the rule. Document any test substring change in your report.

---

### Task 9: Add tests for new rules' presence

**Files to modify:**
- `apps/checkers/_tests/checkers/disk/test_common.py` — add a few tests for new tools (yarn, composer, JetBrains in disk_common, etc.).
- `apps/checkers/_tests/checkers/disk/test_macos.py` — add tests for JETBRAINS, COMPOSER, YARN in macos.
- `apps/checkers/_tests/checkers/disk/test_linux.py` — add a JETBRAINS test.

For each new rule wired into a subclass, add a test in the corresponding `*BuildRecommendationsTests` class verifying the rule fires for a canonical path.

**Step 1: `test_common.py` additions**

Append to `DiskCommonBuildRecommendationsTests`:

```python
    def test_yarn_cache_recommendation(self):
        from apps.checkers.checkers.disk.common import DiskCommonChecker
        checker = DiskCommonChecker()
        space_hogs = [{"path": "/home/me/.cache/yarn/v6/abc", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("yarn cache clean" in line for r in recs for line in r))

    def test_pnpm_cache_recommendation(self):
        from apps.checkers.checkers.disk.common import DiskCommonChecker
        checker = DiskCommonChecker()
        space_hogs = [{"path": "/home/me/.cache/pnpm/store/v3", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("pnpm store prune" in line for r in recs for line in r))

    def test_composer_cache_recommendation(self):
        from apps.checkers.checkers.disk.common import DiskCommonChecker
        checker = DiskCommonChecker()
        space_hogs = [{"path": "/home/me/.cache/composer/repo", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("composer clear-cache" in line for r in recs for line in r))

    def test_gradle_cache_recommendation(self):
        from apps.checkers.checkers.disk.common import DiskCommonChecker
        checker = DiskCommonChecker()
        space_hogs = [{"path": "/home/me/.gradle/caches/old-version", "size_mb": 800.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("gradle --stop" in line for r in recs for line in r))

    def test_cargo_cache_recommendation(self):
        from apps.checkers.checkers.disk.common import DiskCommonChecker
        checker = DiskCommonChecker()
        space_hogs = [{"path": "/home/me/.cargo/registry/cache", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("cargo cache --autoclean" in line for r in recs for line in r))
```

**Step 2: `test_macos.py` additions**

Append to `DiskMacOSBuildRecommendationsTests`:

```python
    def test_jetbrains_recommendation(self):
        from apps.checkers.checkers.disk.macos import DiskMacOSChecker
        checker = DiskMacOSChecker()
        space_hogs = [{"path": "/Users/me/Library/Caches/JetBrains/PyCharm", "size_mb": 3000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("Invalidate Caches" in line for r in recs for line in r))

    def test_composer_recommendation(self):
        from apps.checkers.checkers.disk.macos import DiskMacOSChecker
        checker = DiskMacOSChecker()
        space_hogs = [{"path": "/Users/me/Library/Caches/composer/repo", "size_mb": 3000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("composer clear-cache" in line for r in recs for line in r))

    def test_yarn_recommendation(self):
        from apps.checkers.checkers.disk.macos import DiskMacOSChecker
        checker = DiskMacOSChecker()
        space_hogs = [{"path": "/Users/me/Library/Caches/Yarn/v6/abc", "size_mb": 500.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("yarn cache clean" in line for r in recs for line in r))
```

**Step 3: `test_linux.py` additions**

Append to `DiskLinuxBuildRecommendationsTests`:

```python
    def test_jetbrains_recommendation(self):
        from apps.checkers.checkers.disk.linux import DiskLinuxChecker
        checker = DiskLinuxChecker()
        space_hogs = [{"path": "/home/me/.cache/JetBrains/PyCharm", "size_mb": 2000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("Invalidate Caches" in line for r in recs for line in r))
```

**Step 4: Run new tests**

```bash
uv run pytest apps/checkers/_tests/checkers/disk/test_common.py apps/checkers/_tests/checkers/disk/test_macos.py apps/checkers/_tests/checkers/disk/test_linux.py -v -k "yarn or pnpm or composer or gradle or cargo or jetbrains" 2>&1 | tail -20
```

Expected: 9 new tests PASS.

---

### Task 10: Verify, lint, commit Commit B

**Step 1: Full suite**

```bash
uv run pytest apps/checkers/ -v 2>&1 | tail -10
```

Expected: all PASS.

**Step 2: Coverage**

```bash
uv run coverage run --branch -m pytest apps/checkers/_tests/checkers/disk/ apps/checkers/_tests/test_metrics_format.py
uv run coverage report -m --include='apps/checkers/checkers/disk/*,apps/checkers/management/commands/_metrics_format.py'
```

Expected: 100% on every file.

**Step 3: Lint, format, type-check**

```bash
uv run black --check apps/checkers/
uv run ruff check apps/checkers/
uv run mypy apps/checkers/checkers/ apps/checkers/management/commands/
```

Expected: clean.

**Step 4: Live sanity check (this Mac is darwin)**

```bash
uv run python manage.py check_health disk_macos
```

Verify the recommendations section now includes:
- Multi-line rules render with bullet + indented detail lines.
- Newly-added rules (JetBrains, Composer, etc.) fire when the corresponding paths are present in space_hogs.
- The output is readable and the indentation is consistent.

If no live recommendations fire (because space_hogs paths don't match any keyword), construct a known-positive case with mocked metrics — but in practice the `~/Library/Caches/JetBrains/...` path on this dev machine should fire JETBRAINS.

**Step 5: Commit**

```bash
git add apps/checkers/
git commit -m "$(cat <<'EOF'
feat(checkers): extract recommendation rule constants; add 7 new tools

Move rule definitions into apps/checkers/checkers/disk/recommendations.py
as named constants. Each subclass imports the constants relevant to
its scan paths. No string duplication across cross-platform tools
(yarn, composer, JetBrains, etc. live in one place and are referenced
by both disk_common and disk_macos).

New rules added:
  - YARN, PNPM, COMPOSER, GRADLE, MAVEN, CARGO, GO_MODULES (cross-platform,
    referenced by disk_common; YARN/PNPM/COMPOSER/GRADLE/MAVEN/CARGO also
    referenced by disk_macos for ~/Library/Caches/* matches)
  - JETBRAINS (referenced by disk_macos and disk_linux)

Existing rules (PIP, NPM, HOMEBREW, XCODE, APPLE_CACHES, APT, JOURNAL,
DOCKER, SNAP, LOG_ROTATE, USER_CACHE) gain richer multi-line content
where it helps (e.g., GRADLE includes the daemon-stop step; DOCKER
mentions --volumes; SNAP includes the awk one-liner).

test_recommendations.py adds module-level tests: every rule has the
right shape; no two rules share a title; canonical paths trigger the
right rule. Per-subclass build-recommendation tests cover the new
rules' substrings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass.

---

## Task 11: Push and open stacked PR

```bash
git push -u origin feat/disk-recommendations
gh pr create --base fix/disk-checker-consolidation --title "feat(checkers): richer multi-line disk cleanup recommendations" --body "$(cat <<'EOF'
## Summary
- Changes recommendation_rules from `(keywords, str)` to `(keywords, list[str])` where the second element is `[title, *details]`. CLI renders title as a bullet, details as indented continuation. JSON output (`run_check --json`) emits `list[list[str]]`.
- Extracts rule constants into `apps/checkers/checkers/disk/recommendations.py` so cross-platform tools (yarn, composer, JetBrains, ...) live in one place and are referenced from multiple subclasses without duplication.
- Adds 7 new tool-specific rules: yarn, pnpm, composer, gradle, maven, cargo, go modules, JetBrains. Enriches existing rules (gradle daemon stop step, docker --volumes note, etc.).

**Stacked on #137.** Base is `fix/disk-checker-consolidation`. Retarget to `main` after #137 merges.

Design doc: `docs/plans/2026-05-09-disk-recommendations-design.md`

## Why
Today's recommendations are single-line nudges. A real macOS box surfaces `~/Library/Caches/JetBrains` (3.9 GB) and `~/Library/Caches/composer` (3.3 GB) in the live `disk_macos` run, but no rule fires for either. Users get a pointer but no command. This PR ships actionable multi-step instructions for the tools that actually consume disk on dev machines.

## Visible behavior changes
- **CLI output**: recommendations now render with a title bullet and indented detail lines underneath. Existing single-line advice strings are unchanged in appearance.
- **JSON output**: `metrics["recommendations"]` shape changes from `list[str]` to `list[list[str]]`. **Breaking shape change** — but no JSON consumers exist yet, so no compatibility shim.
- **More rules fire**: machines with yarn / pnpm / composer / gradle / maven / cargo / go module / JetBrains caches will now see actionable cleanup advice that they didn't before.

## Test plan
- [x] `uv run pytest apps/checkers/` — full suite green
- [x] `uv run coverage report` — 100% on disk/* and _metrics_format.py
- [x] black / ruff / mypy clean
- [x] Live `check_health disk_macos` — JetBrains rule fires; multi-line rendering looks right

## Commit structure
Bisect-friendly:
- `<sha>` `refactor(checkers): change recommendation_rules to list-of-lines shape` — pure shape change; same content, no new rules
- `<sha>` `feat(checkers): extract recommendation rule constants; add 7 new tools` — extract + extend

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Stacked on #137.** Keep the base branch as `fix/disk-checker-consolidation` until #137 merges. After merge, retarget to `main` via `gh pr edit <num> --base main`.
- **Two commits, both on this branch.** Commit A is the shape change; Commit B is the extract + extend. Don't squash locally.
- **No new behavior between Commit A and Commit B.** Commit A keeps the same advice strings, just wrapped in 1-element lists. The interesting content lands in Commit B.
- **Test assertion pattern is uniform.** `any("X" in r for r in recs)` becomes `any("X" in line for r in recs for line in r)`. Use grep to find every occurrence; don't try to update them by hand from memory.
- **No CLI flags, no toggles, no thresholds.**
- **No metrics-shape compat shim.** Commit message and PR body explicitly disclose the shape change.
- **`old_files_advice` and `large_files_advice` stay as `str`.** The wrapping into 1-element lists happens inside `_build_recommendations`. Don't change the subclass attribute types.
- **`recommendations.py` rule names are UPPER_SNAKE_CASE.** Module-level constants. No classes, no factories — just tuples bound to names.