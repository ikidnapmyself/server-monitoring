# README Badges Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 10 badges to the top of README.md showing CI status, code quality, tech stack, and project info.

**Architecture:** Pure markdown edit — insert badge rows before the `# server-maintanence` heading. Codacy badge uses a placeholder token until the user provides the real one from their Codacy dashboard.

**Tech Stack:** Markdown, shields.io, GitHub Actions status badges, Codecov, Codacy

---

### Task 1: Add badges to README.md and commit

**Files:**
- Modify: `README.md:1` (insert badge block before the heading)
- Modify: `docs/plans/2026-02-15-readme-badges-design.md` (already exists, no changes needed)

**Step 1: Insert badge block at the top of README.md**

Replace line 1 of `README.md`:

```markdown
# server-maintanence
```

With:

```markdown
[![CI](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/ci.yml)
[![Security](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/security.yml)
[![codecov](https://codecov.io/gh/ikidnapmyself/server-monitoring/graph/badge.svg)](https://codecov.io/gh/ikidnapmyself/server-monitoring)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/CODACY_PROJECT_TOKEN)](https://app.codacy.com/gh/ikidnapmyself/server-monitoring/dashboard)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.2](https://img.shields.io/badge/django-5.2-green.svg)](https://www.djangoproject.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: Ruff](https://img.shields.io/badge/linting-ruff-orange.svg)](https://github.com/astral-sh/ruff)

[![License: MIT](https://img.shields.io/github/license/ikidnapmyself/server-monitoring)](https://github.com/ikidnapmyself/server-monitoring/blob/main/LICENSE)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg)](https://pre-commit.com/)

# server-maintanence
```

**Step 2: Verify the README renders correctly**

Run: `head -15 README.md`
Expected: Badge markdown lines followed by the heading.

**Step 3: Commit**

```bash
git add README.md docs/plans/2026-02-15-readme-badges-design.md
git commit -m "docs: add CI, quality, tech stack, and project badges to README"
```

---

### Post-implementation: Codacy token

After committing, the user needs to:
1. Go to https://app.codacy.com/gh/ikidnapmyself/server-monitoring/dashboard
2. Navigate to Settings > General > Badge
3. Copy the project token
4. Replace `CODACY_PROJECT_TOKEN` in the Codacy badge URL in `README.md`
