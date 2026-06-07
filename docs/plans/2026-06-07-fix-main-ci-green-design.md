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

- **Django target: 6.0.6** (latest), not the 5.2 LTS patch. The Docker image
  already resolves Django 6.0.x, so the app runtime is effectively there already;
  aligning the lock removes drift and clears all five Django CVEs. Accepted risk:
  a 5.2 → 6.0 major bump — the full test/type/check suite must stay green and any
  breakage is in-scope to fix.
- **Codecov: pin the action to a known-good commit SHA** (matching the repo's
  existing SHA-pin convention for `setup-uv`/`trivy-action`), paired with pinning
  the Codecov CLI `version:` input — because the GPG failure is in the downloaded
  CLI, not the action wrapper. Kept blocking (`fail_ci_if_error: true` stays).
- **Scope: fix all three** so both CI and Security pass on `main`.

## Changes

1. **`pyproject.toml`** — `django>=5.2.14` → `django>=6.0.6` (raise
   `django-stubs` floor if 6.0 requires it).
2. **`uv.lock`** — `uv lock --upgrade-package django --upgrade-package aiohttp
   --upgrade-package pip` (and `django-object-actions` / `django-json-widget` if
   the resolver needs the 6.0-compatible versions the image already uses:
   object-actions 5.0.0, json-widget 2.1.1).
3. **`.github/workflows/security.yml`** — add `ignore-unfixed: true` to the
   CRITICAL trivy step.
4. **`.github/workflows/ci.yml`** — pin `codecov/codecov-action` to a release SHA
   (with `# vX.Y.Z` comment) and pin the CLI `version:`.
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