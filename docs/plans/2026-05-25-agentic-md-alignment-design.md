---
title: "2026-05-25 Align Agentic MD Files (CLAUDE.md, AGENTS.md, GEMINI.md)"
parent: Plans
---
# Align Agentic Markdown Files

## Context

The repo carries two parallel sources of agent guidance at the root: `CLAUDE.md` (140 lines) and `agents.md` (346 lines). Six more `agents.md` files live under `apps/` and `bin/`, totalling **1146 lines of agentic markdown**. Substantive overlap between the two root files (skills tables, pipeline architecture, absolute-paths rule, test-coverage rule, code conventions) means edits land in one file but not the other and the two drift. Adding a third tool (Gemini CLI, Codex) today would require manually copying the same content into a third file.

## Goal

A single canonical source for tool-agnostic agent guidance; thin per-tool entry files that import it; zero content duplication.

## Approach

Adopt the cross-tool `AGENTS.md` (uppercase, root) convention as the canonical file. Tool-specific entry files become shims that import it via Claude Code's `@path` directive.

### File layout after

| File | Role | Maintenance pattern |
|---|---|---|
| `AGENTS.md` (root) | Canonical, tool-agnostic. Holds project overview, essential commands, architecture, pipeline contracts, conventions, GH Pages rules, agent roles. | Editable by anyone; only file touched for repo-wide convention changes. |
| `CLAUDE.md` (root) | Claude Code intro line + `@AGENTS.md` + Skills (Superpowers) section. | Touched only when Claude Code-specific guidance changes. |
| `GEMINI.md` (root) | Gemini CLI intro + `@AGENTS.md`. No Gemini-specific content yet — future-proofing. | Touched only if Gemini-specific guidance appears. |
| `apps/<app>/AGENTS.md` (x5) | App-local guidance (unchanged content; rename from lowercase). | Editable when app-specific contracts change. |
| `bin/AGENTS.md` | CLI script guidance (unchanged content; rename from lowercase). | Editable when bin/ behaviour changes. |

### Content consolidation (root)

Move from `CLAUDE.md` → `AGENTS.md`:
- Essential Commands block
- Architecture section (Pipeline Flow, App Structure, Core Apps table, Key Patterns)
- Code Conventions (merge into existing Best Practices in agents.md, dedupe overlapping rules: absolute paths, test coverage, imports)
- Documentation & GitHub Pages section

Stays in `CLAUDE.md`:
- Intro: `This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.`
- Skills (Superpowers) table (slash commands, the 1% rule)

### Cross-reference updates

Live references that must update in the same commit:
- `README.md` (two mentions of `agents.md`)
- `docs/Security.md` (one mention of "each app's `agents.md`")
- Internal references inside the canonical `AGENTS.md` to `apps/*/agents.md` (rename to `apps/*/AGENTS.md`)

Historical references in `docs/plans/*.md` are **not** touched — per project convention plan docs are immutable historical records. They correctly reference the file name as it existed when the plan was written.

### Rename strategy on macOS

`agents.md` → `AGENTS.md` is a case-only rename. Default macOS filesystems are case-insensitive, so a naive `mv` is a no-op. Use the two-step Git rename to make the change visible on case-sensitive systems (Linux CI, most Git remote configurations):

```bash
git mv agents.md agents.md.tmp
git mv agents.md.tmp AGENTS.md
```

Same pattern for each `apps/<app>/agents.md` and `bin/agents.md`.

## Rejected alternatives

- **Plain prose reference, no `@` import.** Each tool file says "see AGENTS.md". The model has to remember to read it; drift risk in practice. Rejected — `@` import is the lower-maintenance mechanism Claude Code supports natively.
- **Filesystem symlink CLAUDE.md → AGENTS.md.** Guaranteed byte-identical, but leaves no room for the Skills section that belongs only in Claude Code's context, and symlinks don't survive Windows checkouts. Rejected.
- **Canonical at `docs/AgentGuide.md`.** Cleaner separation of "canonical" from "entry point", but adds indirection without a corresponding benefit, and breaks the AGENTS.md auto-discovery convention used by other tools (Codex). Rejected.

## Risk

Low. The rename is a content-preserving move; entry files import via a documented Claude Code mechanism; no runtime code paths reference these files. The single risk is that a cross-reference is missed in the rename — mitigated by grep-driven verification in the implementation plan.

## Done criteria

1. `AGENTS.md` (capital, root) exists and contains the consolidated tool-agnostic content with no duplication.
2. `CLAUDE.md` is reduced to intro + `@AGENTS.md` + Skills section.
3. `GEMINI.md` exists as a thin shim.
4. All app-level and `bin/` agents.md files are renamed to `AGENTS.md` (case-sensitive rename).
5. All live (non-historical) cross-references in `README.md`, `docs/Security.md`, and inside `AGENTS.md` itself point to the new file names.
6. `grep -rn 'agents\.md' --exclude-dir=docs/plans --exclude-dir=.venv` returns zero hits outside historical plan docs.