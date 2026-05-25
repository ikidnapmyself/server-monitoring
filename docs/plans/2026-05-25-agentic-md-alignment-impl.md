---
title: "2026-05-25 Align Agentic MD Files — Implementation"
parent: Plans
---
# Align Agentic Markdown Files — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collapse the duplicated `CLAUDE.md` + `agents.md` content into a single canonical `AGENTS.md`, with `CLAUDE.md` and a new `GEMINI.md` as thin entry shims that `@`-import it.

**Architecture:** Five focused commits — (1) rewrite the canonical content under the current `agents.md` path, (2) slim `CLAUDE.md` and add `GEMINI.md` as `@`-import shims, (3) case-rename every `agents.md` to `AGENTS.md`, (4) update live cross-references in `README.md` / `docs/Security.md`, (5) final grep audit. Historical `docs/plans/*` references stay untouched (immutable record).

**Tech Stack:** Markdown only. No runtime code; verification is `grep` plus visual inspection of the rendered files.

**Note on TDD:** This is a documentation refactor. The "failing test" for each task is a `grep` that confirms a stale reference still exists; the "passing test" is the same `grep` returning zero. Every task ends with a verification command and an expected output.

**Working branch:** `chore/align-agentic-md-files` (already checked out; design doc committed in `90913a5`).

---

### Task 1: Audit baseline cross-references

**Files:** none (read-only).

**Step 1: Snapshot every live reference to `agents.md`**

Run:
```bash
grep -rEn 'agents\.md|AGENTS\.md' \
  --include='*.md' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.toml' \
  . 2>/dev/null \
  | grep -v '^\./\.venv' | grep -v '^\./node_modules' \
  | grep -v '^\./docs/plans/' \
  > /tmp/agents-md-refs-before.txt
wc -l /tmp/agents-md-refs-before.txt
```

Expected: a non-zero count (currently ~20 lines across `README.md`, `agents.md`, `CLAUDE.md`, `docs/Security.md`). The `docs/plans/` exclusion is critical — those are immutable historical records that must not be touched.

This snapshot is the reference set Task 4 must reduce to zero remaining `agents\.md` (lowercase) matches.

---

### Task 2: Rewrite `agents.md` as the canonical, tool-agnostic source

**Files:**
- Modify: `agents.md`

**Step 1: Edit `agents.md`**

Treat the existing `agents.md` (346 lines) as the base. Merge in the tool-agnostic sections currently duplicated or unique to `CLAUDE.md`, then dedupe. The final structure should be:

```
# Project Agent Guide

(1-paragraph intro — this file is the canonical, tool-agnostic source;
 tool-specific files like CLAUDE.md and GEMINI.md @-import it.)

## Documentation map
- AGENTS.md (this file) — repo-wide guidance
- CLAUDE.md / GEMINI.md — tool-specific entry shims
- apps/<app>/AGENTS.md — app-local contracts
- docs/Architecture.md, docs/Security.md, etc.

## Project overview
(1 paragraph — from CLAUDE.md "Project Overview")

## Essential commands
(bash block — verbatim from CLAUDE.md "Essential Commands")

## Architecture
### Pipeline flow
(from CLAUDE.md, with the existing deeper "Orchestration Flow"
 section from agents.md merged in — keep one place that explains
 the trace_id / run_id contract)
### App structure
(from CLAUDE.md "App Structure")
### Core apps table
(from CLAUDE.md "Core Apps")
### Key patterns
(from CLAUDE.md "Key Patterns")

## Agent roles (Plan / Coder / Debug / Review / Docs)
(from existing agents.md, unchanged)

## Pipeline-level rules
- Boundary rule (orchestrator-only stage transitions)
- Monitoring tool requirements
- Failure & retry policy
- Mental model
(from existing agents.md, unchanged)

## Conventions and best practices
(MERGE: agents.md "Best Practices" + CLAUDE.md "Code Conventions".
 Dedupe: absolute-paths rule, 100% branch coverage, absolute imports,
 100-char line length, uv usage — each rule appears EXACTLY once.)

## Tooling and CI
(from existing agents.md, unchanged)

## Documentation and GitHub Pages
(from CLAUDE.md, unchanged — Jekyll front-matter, raw blocks,
 title-case docs filenames)

## Definition of done
(from existing agents.md, unchanged)

## Quick reference table
(from existing agents.md, unchanged)
```

**Deduplication checklist** — each of these rules must appear in exactly one place after the rewrite:

- Absolute paths via `pathlib.Path.resolve()` (currently in both CLAUDE.md and agents.md)
- 100% branch coverage (currently in both)
- Absolute imports `from apps.X.Y import Z` (currently in both)
- 100-character line length (currently in both)
- `uv add` / `uv sync --extra dev` (currently in both)
- Pipeline flow diagram (currently brief in CLAUDE.md, expanded in agents.md — keep expanded version only)

Internal cross-reference links inside the rewritten file should still point to `apps/<app>/agents.md` (lowercase) for now — Task 4 will update them to `AGENTS.md`.

**Step 2: Verify the file still parses as Markdown**

Run: `head -1 agents.md && wc -l agents.md`

Expected: first line is `# Project Agent Guide` (or similar canonical header); line count somewhere in the 400–500 range (current 346 + merged ~80 unique lines from CLAUDE.md − overlap removed).

**Step 3: Commit**

```bash
git add agents.md
git commit -m "$(cat <<'EOF'
docs: consolidate tool-agnostic guidance into agents.md

Merge the Essential Commands, Architecture (App Structure / Core Apps
table / Key Patterns), GitHub Pages, and Code Conventions sections
from CLAUDE.md into agents.md so this file becomes the single
canonical source for tool-agnostic project guidance. Dedupe overlapping
rules (absolute paths, branch coverage, imports, line length, uv usage)
so each appears exactly once. CLAUDE.md is slimmed down to a thin
@-import shim in the next commit.
EOF
)"
```

Expected: pre-commit hooks (black/ruff/pytest/mypy) all pass — no Python touched.

---

### Task 3: Slim `CLAUDE.md` and create `GEMINI.md` as shims

**Files:**
- Modify: `CLAUDE.md`
- Create: `GEMINI.md`

**Step 1: Replace `CLAUDE.md` entirely with the shim**

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

All tool-agnostic project guidance lives in `agents.md` and is imported below.

@agents.md

## Skills (Superpowers — Claude Code only)

Use these skills via `/skill-name` commands for disciplined workflows:

| Skill | When to Use |
|-------|-------------|
| `/brainstorming` | **Before any creative work** — new features, components, behavior changes |
| `/writing-plans` | When you have requirements for a multi-step task |
| `/executing-plans` | Execute a written plan in a separate session |
| `/test-driven-development` | Before writing implementation code |
| `/systematic-debugging` | When encountering bugs, test failures, unexpected behavior |
| `/verification-before-completion` | Before claiming work is done — run tests, confirm output |
| `/requesting-code-review` | After completing tasks or major features |
| `/receiving-code-review` | When receiving review feedback |
| `/using-git-worktrees` | For isolated feature work |
| `/finishing-a-development-branch` | When ready to merge/PR |
| `/dispatching-parallel-agents` | For 2+ independent tasks |
| `/subagent-driven-development` | Execute plans with independent tasks |

**Rule:** If there's even a 1% chance a skill applies, invoke it first.
```

**Step 2: Create `GEMINI.md`**

```markdown
# GEMINI.md

This file provides guidance to Gemini CLI when working with code in this repository.

All tool-agnostic project guidance lives in `agents.md` and is imported below.

@agents.md
```

(No Gemini-specific section yet — added when Gemini-specific guidance accumulates.)

**Step 3: Verify the shim sizes**

Run: `wc -l CLAUDE.md GEMINI.md`

Expected: `CLAUDE.md` ~30 lines (vs. previous 140), `GEMINI.md` ~7 lines.

**Step 4: Commit**

```bash
git add CLAUDE.md GEMINI.md
git commit -m "$(cat <<'EOF'
docs: slim CLAUDE.md to @-import shim, add GEMINI.md

CLAUDE.md becomes a thin entry file: Claude Code intro, an @agents.md
import directive that Claude Code inlines automatically, and the
Skills/Superpowers section (Claude Code-specific, no home in the
agent-agnostic canonical). GEMINI.md mirrors the pattern for Gemini
CLI — same @-import, no Gemini-specific content yet (future-proofing).

Maintenance: tool-agnostic guidance edits land in agents.md only;
the shims change only when tool-specific guidance does.
EOF
)"
```

---

### Task 4: Case-rename every `agents.md` to `AGENTS.md`

**Files:**
- Rename: `agents.md` → `AGENTS.md`
- Rename: `apps/alerts/agents.md` → `apps/alerts/AGENTS.md`
- Rename: `apps/checkers/agents.md` → `apps/checkers/AGENTS.md`
- Rename: `apps/intelligence/agents.md` → `apps/intelligence/AGENTS.md`
- Rename: `apps/notify/agents.md` → `apps/notify/AGENTS.md`
- Rename: `apps/orchestration/agents.md` → `apps/orchestration/AGENTS.md`
- Rename: `bin/agents.md` → `bin/AGENTS.md`
- Modify: `CLAUDE.md` (change `@agents.md` → `@AGENTS.md`)
- Modify: `GEMINI.md` (change `@agents.md` → `@AGENTS.md`)
- Modify: `AGENTS.md` (internal references to `apps/*/agents.md` → `apps/*/AGENTS.md`)

**Step 1: Case-rename each file via two-step Git rename**

macOS uses a case-insensitive filesystem by default, so a direct `git mv agents.md AGENTS.md` is a no-op locally. Use the two-step pattern so the rename is recorded explicitly and is correct on case-sensitive filesystems (Linux CI):

```bash
for path in agents.md apps/alerts/agents.md apps/checkers/agents.md \
            apps/intelligence/agents.md apps/notify/agents.md \
            apps/orchestration/agents.md bin/agents.md; do
  dir=$(dirname "$path")
  git mv "$path" "$dir/agents.md.tmp"
  git mv "$dir/agents.md.tmp" "$dir/AGENTS.md"
done
git status
```

Expected: `git status` shows 7 renames (one per file), all from `agents.md` to `AGENTS.md`.

**Step 2: Update `@`-imports in `CLAUDE.md` and `GEMINI.md`**

Edit `CLAUDE.md`: change the single line `@agents.md` → `@AGENTS.md`.
Edit `GEMINI.md`: change the single line `@agents.md` → `@AGENTS.md`.

**Step 3: Update internal references inside `AGENTS.md`**

The "Documentation map" and "Where stage-specific contracts live" sections inside the new `AGENTS.md` reference `apps/<app>/agents.md` in 9 places. Replace each with `apps/<app>/AGENTS.md`. Use:

```bash
grep -n 'apps/.*agents\.md' AGENTS.md
```

Expected: a list of lines (~9). Edit each to use `AGENTS.md` (capital). After editing, re-run the grep:

```bash
grep -n 'apps/.*agents\.md' AGENTS.md
```

Expected: zero output.

**Step 4: Commit**

```bash
git add CLAUDE.md GEMINI.md AGENTS.md apps/*/AGENTS.md bin/AGENTS.md
git commit -m "$(cat <<'EOF'
docs: case-rename agents.md → AGENTS.md across the repo

Adopt the cross-tool AGENTS.md (capital) convention — auto-discovered
by OpenAI Codex and the canonical name in most agent-tooling docs.
Case-only renames are done two-step (mv → .tmp → AGENTS.md) so the
change is visible on case-sensitive filesystems even when authored
on a case-insensitive macOS checkout.

Updates the @ imports in CLAUDE.md and GEMINI.md, and the 9 internal
references inside AGENTS.md to apps/*/AGENTS.md.
EOF
)"
```

---

### Task 5: Update live cross-references in `README.md` and `docs/Security.md`

**Files:**
- Modify: `README.md` (two mentions of `agents.md`)
- Modify: `docs/Security.md` (one mention of "each app's `agents.md`")

**Step 1: List the references to update**

Run:
```bash
grep -nE 'agents\.md' README.md docs/Security.md
```

Expected: 3 hits (2 in README.md, 1 in docs/Security.md).

**Step 2: Edit each in place**

For `README.md`:
- Line ~40: the markdown link `[`agents.md`](agents.md)` → `[`AGENTS.md`](AGENTS.md)`
- Line ~185: the prose mention `agents.md describes` → `AGENTS.md describes`

For `docs/Security.md`:
- Line ~27: `each app's `agents.md`` → `each app's `AGENTS.md``

**Step 3: Verify**

```bash
grep -nE 'agents\.md' README.md docs/Security.md
```

Expected: zero output.

**Step 4: Commit**

```bash
git add README.md docs/Security.md
git commit -m "$(cat <<'EOF'
docs: update README and Security.md references to AGENTS.md

Live mentions of agents.md (lowercase) now point at the renamed
AGENTS.md (capital). Historical references in docs/plans/* are
intentionally not touched — those are immutable records of the
file name as it existed when each plan was written.
EOF
)"
```

---

### Task 6: Final audit — zero stale references outside `docs/plans/`

**Files:** none.

**Step 1: Run the audit grep**

```bash
grep -rEn 'agents\.md' \
  --include='*.md' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.toml' \
  . 2>/dev/null \
  | grep -v '^\./\.venv' | grep -v '^\./node_modules' \
  | grep -v '^\./docs/plans/' \
  > /tmp/agents-md-refs-after.txt
wc -l /tmp/agents-md-refs-after.txt
cat /tmp/agents-md-refs-after.txt
```

Expected: zero lines. If anything appears, investigate and fix in a new commit before considering this plan done.

**Step 2: Spot-check that `@AGENTS.md` import resolves**

Open `CLAUDE.md` in any editor. Confirm:
- Intro line about Claude Code is present.
- The `@AGENTS.md` directive is on its own line.
- The Skills (Superpowers) table is intact.

Open `AGENTS.md`. Confirm:
- Pipeline flow, Essential Commands, App Structure, Core Apps, Key Patterns sections are all present.
- No `agents.md` (lowercase) references inside.

**Step 3: Sanity-run pre-commit on all touched files**

```bash
uv run pre-commit run --files CLAUDE.md AGENTS.md GEMINI.md README.md docs/Security.md
```

Expected: all hooks pass (markdownlint is not configured, so this is essentially a no-op for markdown files but exercises the hook chain).

No commit on this task — pure verification.

---

## Done criteria

1. `AGENTS.md` (capital) exists at root with the consolidated canonical content; `agents.md` (lowercase) no longer exists.
2. `CLAUDE.md` is ~30 lines: intro + `@AGENTS.md` + Skills table.
3. `GEMINI.md` exists as a ~7-line shim.
4. All 6 app-level / `bin/` `agents.md` files are renamed to `AGENTS.md`.
5. `README.md` and `docs/Security.md` reference `AGENTS.md` (capital), not the lowercase form.
6. The Task 6 grep returns zero hits.

## Skills referenced

- @superpowers:verification-before-completion — every task ends with a concrete grep / wc verification.
- @superpowers:executing-plans — execution harness for this plan.