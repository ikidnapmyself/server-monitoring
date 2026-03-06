---
title: "GitHub Pages Implementation Plan"
parent: Plans
nav_exclude: true
---
# GitHub Pages Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy project documentation as a GitHub Pages site using Jekyll + Just the Docs theme via GitHub Actions.

**Architecture:** Jekyll builds from `docs/` directory. GitHub Actions workflow builds and deploys on push to `main`. Existing markdown files get Jekyll front matter for navigation. `wiki.md` is renamed to `Index.md` (title case consistency) and serves as the homepage.

**Tech Stack:** Jekyll, Just the Docs theme (remote), GitHub Actions (`actions/configure-pages`, `actions/jekyll-build-pages`, `actions/deploy-pages`)

---

### Task 1: Create Jekyll config

**Files:**
- Create: `docs/_config.yml`

**Step 1: Create the Jekyll configuration file**

```yaml
title: server-maintanence
description: Django-based server monitoring and alerting system
remote_theme: just-the-docs/just-the-docs

url: https://ikidnapmyself.github.io
baseurl: /server-monitoring

permalink: pretty

aux_links:
  "GitHub":
    - "https://github.com/ikidnapmyself/server-monitoring"

nav_sort: order

color_scheme: dark
```

**Step 2: Commit**

```bash
git add docs/_config.yml
git commit -m "docs: add Jekyll config for GitHub Pages with Just the Docs theme"
```

---

### Task 2: Rename wiki.md to Index.md and add front matter

**Files:**
- Rename: `docs/wiki.md` → `docs/Index.md`
- Modify: `docs/Index.md:1` (prepend front matter)

**Step 1: Rename the file**

```bash
git mv docs/wiki.md docs/Index.md
```

**Step 2: Add Jekyll front matter to the top of `docs/Index.md`**

Prepend these lines before the existing `# Project Wiki` heading:

```yaml
---
title: Home
layout: default
nav_order: 1
permalink: /
---
```

**Step 3: Commit**

```bash
git add docs/Index.md
git commit -m "docs: rename wiki.md to Index.md, add Jekyll front matter"
```

---

### Task 3: Add front matter to existing docs

**Files:**
- Modify: `docs/Architecture.md:1` (prepend front matter)
- Modify: `docs/Installation.md:1` (prepend front matter)
- Modify: `docs/Setup-Guide.md:1` (prepend front matter)
- Modify: `docs/Security.md:1` (prepend front matter)
- Modify: `docs/Templates.md:1` (prepend front matter)

**Step 1: Add front matter to `docs/Architecture.md`**

Prepend before `# Architecture`:

```yaml
---
title: Architecture
layout: default
nav_order: 2
---
```

**Step 2: Add front matter to `docs/Installation.md`**

Prepend before `# Installation`:

```yaml
---
title: Installation
layout: default
nav_order: 3
---
```

**Step 3: Add front matter to `docs/Setup-Guide.md`**

Prepend before `# Setup Guide`:

```yaml
---
title: Setup Guide
layout: default
nav_order: 4
---
```

**Step 4: Add front matter to `docs/Security.md`**

Prepend before `# Security`:

```yaml
---
title: Security
layout: default
nav_order: 5
---
```

**Step 5: Add front matter to `docs/Templates.md`**

Prepend before `# Templates`:

```yaml
---
title: Templates
layout: default
nav_order: 6
---
```

**Step 6: Commit**

```bash
git add docs/Architecture.md docs/Installation.md docs/Setup-Guide.md docs/Security.md docs/Templates.md
git commit -m "docs: add Jekyll front matter to all existing doc pages"
```

---

### Task 4: Create Plans index page

**Files:**
- Create: `docs/plans/Index.md`

**Step 1: Create the plans index page**

This page serves as the parent nav item for all plan documents and tells the story of how the project was built.

Create `docs/plans/Index.md` with front matter and an introduction that lists all plans chronologically. The page should:
- Have `nav_order: 7` and `has_children: true`
- Open with a paragraph celebrating that every feature was designed before it was built — 46 design and implementation plans spanning 6 weeks of disciplined development
- List all plan files as links grouped by date

**Step 2: Add front matter to each plan file**

Each plan file in `docs/plans/` (except `Index.md`) needs front matter with `parent: Plans` and `nav_exclude: true` so they're accessible via the index but don't clutter the top nav. Use a script:

```bash
for f in docs/plans/2026-*.md; do
  title=$(head -1 "$f" | sed 's/^# //')
  sed -i '' "1i\\
---\\
title: \"$title\"\\
parent: Plans\\
nav_exclude: true\\
---\\
" "$f"
done
```

**Step 3: Commit**

```bash
git add docs/plans/
git commit -m "docs: add Plans index page and front matter to all plan files"
```

---

### Task 5: Create GitHub Actions workflow

**Files:**
- Create: `.github/workflows/pages.yml`

**Step 1: Create the workflow file**

```yaml
name: Deploy docs to GitHub Pages

on:
  push:
    branches: ["main"]
    paths:
      - "docs/**"
      - ".github/workflows/pages.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/configure-pages@v5

      - uses: actions/jekyll-build-pages@v1
        with:
          source: ./docs
          destination: ./_site

      - uses: actions/upload-pages-artifact@v3

  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    needs: build
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

**Step 2: Commit**

```bash
git add .github/workflows/pages.yml
git commit -m "ci: add GitHub Actions workflow for GitHub Pages deployment"
```

---

### Task 6: Update README.md with docs site link

**Files:**
- Modify: `README.md:21-25`

**Step 1: Update the documentation map section**

Replace lines 21-25 of `README.md` with:

```markdown
## Documentation map

- **Full project wiki: [ikidnapmyself.github.io/server-monitoring](https://ikidnapmyself.github.io/server-monitoring/)**
- Architecture: [`docs/Architecture.md`](docs/Architecture.md)
- Installation: [`docs/Installation.md`](docs/Installation.md)
- Security: [`docs/Security.md`](docs/Security.md)
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add GitHub Pages link to README documentation map"
```

---

### Task 7: Manual step — Enable GitHub Pages in repo settings

This step cannot be automated via code. After pushing:

1. Go to **github.com/ikidnapmyself/server-monitoring/settings/pages**
2. Under **Build and deployment > Source**, select **GitHub Actions**
3. The workflow will deploy on the next push to `main` that touches `docs/`

---