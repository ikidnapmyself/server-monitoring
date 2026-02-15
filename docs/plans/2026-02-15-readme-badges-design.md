# README Badges Design

**Goal:** Add a comprehensive badge set to the top of `README.md` to signal project health, quality, and tech stack at a glance.

**Layout:** Three rows grouped by category, all using shields.io flat style for visual consistency.

---

## Badge Set

### Row 1: CI / Quality

| Badge | Service | URL Pattern |
|-------|---------|-------------|
| CI (build) | GitHub Actions `ci.yml` | `github/actions/workflow/status/ikidnapmyself/server-monitoring/ci.yml?branch=main` |
| Security | GitHub Actions `security.yml` | `github/actions/workflow/status/ikidnapmyself/server-monitoring/security.yml?branch=main` |
| Codecov | codecov.io | `codecov/c/github/ikidnapmyself/server-monitoring` |
| Codacy grade | codacy.com | `codacy/grade/{PROJECT_TOKEN}` (set up at codacy.com, grab badge markdown) |

### Row 2: Tech Stack

| Badge | Type | Value |
|-------|------|-------|
| Python | Static | `python-3.10+-blue` |
| Django | Static | `django-5.2-green` |
| Code style | Static | `code%20style-black-000000` |
| Linting | Static | `linting-ruff-orange` |

### Row 3: Project Info

| Badge | Source | Value |
|-------|--------|-------|
| License | GitHub API | `github/license/ikidnapmyself/server-monitoring` |
| Pre-commit | Static | `pre--commit-enabled-brightgreen` with pre-commit.com link |

---

## Placement

Badges go at the very top of `README.md`, before the `# server-maintanence` heading. Each row separated by a newline. Badges within a row separated by spaces.

## Notes

- Codacy badge URL contains a project-specific token from codacy.com dashboard. Use placeholder until token is provided.
- Codecov is already integrated in CI (`ci.yml` uploads `coverage.xml`).
- All dynamic badges auto-update on push/PR.
