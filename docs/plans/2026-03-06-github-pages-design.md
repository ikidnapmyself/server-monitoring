---
title: "2026-03-06 GitHub Pages Design"
parent: Plans
---
# GitHub Pages Design

## Goal

Create a GitHub Pages documentation site using Jekyll + Just the Docs theme, deployed via GitHub Actions workflow.

## Approach

GitHub Actions workflow (Approach B) — `.github/workflows/pages.yml` builds Jekyll from `docs/` and deploys to GitHub Pages.

## File Changes

### New Files

- `docs/_config.yml` — Jekyll config with Just the Docs remote theme
- `.github/workflows/pages.yml` — Build and deploy workflow

### Renamed Files

- `docs/wiki.md` → `docs/Index.md` (title case consistency, serves as homepage)

### Modified Files (add Jekyll front matter)

- `docs/Index.md` — `nav_order: 1`, `permalink: /`
- `docs/Architecture.md` — `nav_order: 2`
- `docs/Installation.md` — `nav_order: 3`
- `docs/Setup-Guide.md` — `nav_order: 4`
- `docs/Security.md` — `nav_order: 5`
- `docs/Templates.md` — `nav_order: 6`
- `README.md` — Add link to wiki/GitHub Pages in documentation map

### Navigation Order

1. Project Wiki (Index.md — homepage)
2. Architecture
3. Installation
4. Setup Guide
5. Security
6. Templates

## Jekyll Config

- Theme: `just-the-docs` (remote theme)
- Source: `docs/` directory
- Title: "server-maintanence"
- Permalink style: pretty

## GitHub Actions Workflow

- Triggers: push to `main` (paths: `docs/**`)
- Uses `actions/jekyll-build-pages` + `actions/deploy-pages`
- Deploys to GitHub Pages environment

## README Updates

- Root `README.md`: Add GitHub Pages link in documentation map
- Per-app READMEs: Link back to full wiki where relevant