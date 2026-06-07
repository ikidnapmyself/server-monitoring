---
title: "Fix main: green CI + Security"
parent: Plans
---

# Fix `main` — green CI + Security — Design

**Status:** approved design
**Date:** 2026-06-07
**Branch:** `fix/main-ci-green` → PR → merge (never push `main` directly).

## Context

After PR #165 merged, `main` is red. Investigation shows **three independent
causes, none introduced by #165** (all 7 substantive jobs — BATS, Lint, Django
checks, Type Check, Security-check, Tests 3.11/3.12 — pass):

| # | Failing check | Root cause | Nature |
|---|---|---|---|
| 1 | CI / Test (3.10) → Upload to Codecov | Codecov CLI GPG signature verification fails (`gpg: Can't check signature: No public key`); the step has `fail_ci_if_error: true` | Upstream `codecov-action`/CLI regression |
| 2 | Security / Code Security → pip-audit `--strict` | 8 CVEs in 3 packages | Stale deps (all have fixes) |
| 3 | Security / Docker Security → trivy (CRITICAL) | 2 CRITICAL CVEs in `perl-base` (CVE-2026-42496, CVE-2026-8376), both `fix_deferred`/`affected` with **no released fix** | Unfixable base-image OS vulns |

pip-audit CVEs (#2): `django` 5.2.14 → fix 5.2.15/6.0.6 (PYSEC-2026-197/198/199/200/201),
`aiohttp` 3.13.5 → 3.14.0 (CVE-2026-34993, CVE-2026-47265), `pip` 26.1.1 → 26.1.2
(PYSEC-2026-196).

The Security workflow was already red before #165; the Codecov breakage is recent
and upstream. This change makes `main` fully green.

## Decisions (chosen)

- **Django target: 5.2.15 LTS**, pinned `>=5.2.15,<6.0`. An initial attempt to
  jump to Django 6.0.6 was rejected: Django 6.0 requires Python >=3.12, which
  conflicts with the project's `requires-python = ">=3.10"` (uv: "django==6.0.6
  depends on Python>=3.12 … requirements are unsatisfiable"). We keep Python 3.10
  support, so we stay on the 5.2 LTS line. 5.2.15 fixes all five Django CVEs;
  the `<6.0` cap stops the resolver from re-selecting 6.0.x and re-triggering the
  Python-floor conflict. `requires-python`, the CI matrix (3.10/3.11/3.12), and
  the black/ruff/mypy targets are all unchanged.
- **Codecov: pin the action to a known-good commit SHA** — `codecov-action`
  `@e53489f… # v7.0.0`, superseding the floating `@v5` whose bundled CLI failed
  GPG signature verification of the downloaded uploader. Matches the repo's
  existing SHA-pin convention (`setup-uv`, `trivy-action`). Kept blocking
  (`fail_ci_if_error: true` stays). Only confirmable on the PR's own CI run.
- **Scope: fix all three** so both CI and Security pass on `main`.

## Changes

1. **`pyproject.toml`** — `django>=5.2.14` → `django>=5.2.15,<6.0`;
   `django-stubs>=5.0.0` (unchanged from the original floor). `requires-python`
   stays `>=3.10`.
2. **`uv.lock`** — `uv lock --upgrade-package django --upgrade-package aiohttp
   --upgrade-package pip` → django 5.2.15, aiohttp 3.14.0, pip 26.1.2.
3. **`.github/workflows/security.yml`** — add `ignore-unfixed: true` to the
   CRITICAL trivy step.
4. **`.github/workflows/ci.yml`** — pin `codecov/codecov-action` to the v7.0.0
   release SHA (with `# v7.0.0` comment).
5. **`docs/Security.md`** — note the trivy `ignore-unfixed` rationale (unfixed
   upstream OS CVEs cannot be patched and must not block all merges).

## Out of scope

- `docs/plans/*` historical records — unchanged.
- Reworking the Codecov integration beyond pinning; tokenless/OIDC migration is a
  separate concern.

## Verification

- **Local (fully verifiable):** `uv run pip-audit --strict --desc` clean;
  `uv run python manage.py check`; `uv run pytest`; `uv run mypy .`;
  `uv run black . --check`; `uv run ruff check .`; bats still 146/146.
- **CI-only (not locally verifiable):** the Codecov GPG fix (#4) and the trivy
  `ignore-unfixed` result (#3) are confirmed by the PR's own CI/Security run;
  iterate there if still red.

## Done

- Both CI and Security workflows green on the PR.
- Merged to `main` via PR.