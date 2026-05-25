# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

All tool-agnostic project guidance lives in `AGENTS.md` and is imported below.

@AGENTS.md

## Skills (Superpowers — Claude Code only)

Use these skills via `/skill-name` commands for disciplined workflows.

| Skill | When to use |
|---|---|
| `/brainstorming` | **Before any creative work** — new features, components, behaviour changes |
| `/writing-plans` | When you have requirements for a multi-step task |
| `/executing-plans` | Execute a written plan in a separate session |
| `/test-driven-development` | Before writing implementation code |
| `/systematic-debugging` | When encountering bugs, test failures, unexpected behaviour |
| `/verification-before-completion` | Before claiming work is done — run tests, confirm output |
| `/requesting-code-review` | After completing tasks or major features |
| `/receiving-code-review` | When receiving review feedback |
| `/using-git-worktrees` | For isolated feature work |
| `/finishing-a-development-branch` | When ready to merge / PR |
| `/dispatching-parallel-agents` | For 2+ independent tasks |
| `/subagent-driven-development` | Execute plans with independent tasks |

**Rule:** if there's even a 1% chance a skill applies, invoke it first.