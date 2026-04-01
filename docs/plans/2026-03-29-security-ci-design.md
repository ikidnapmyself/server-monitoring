---
title: "2026-03-29 Security CI Pipeline"
parent: Plans
nav_order: 79739670
---

# Security CI Pipeline — Design

**Date:** 2026-03-29

## Problem

The CI pipeline has Django security checks and Codacy static analysis, but no dependency vulnerability scanning, Python security linting, secret detection, or Docker image scanning.

## Goal

Add a dedicated `security.yml` GitHub Actions workflow with two parallel jobs that catch security issues before they reach production.

## Triggers

- **Main push:** Always runs all scans (blocks production if red)
- **PR:** Only when relevant files change (Python files, pyproject.toml, uv.lock, Dockerfile, docker-compose.yml, or security.yml itself)

## Jobs

### Job 1: Code Security

Runs three scans sequentially:

1. **pip-audit** — checks Python dependencies against known vulnerability databases (PyPI advisory DB). `--strict` fails on any known vuln.
2. **bandit** — Python security anti-pattern linter. Catches eval(), subprocess with shell=True, hardcoded passwords, insecure hashing, etc. Scans `apps/` and `config/`.
3. **detect-secrets** — scans all files for accidentally committed secrets, API keys, tokens. Excludes `.env.sample` and `uv.lock` (false positives).

### Job 2: Docker Security

1. Builds the Docker image from `deploy/docker/Dockerfile`
2. **trivy** — scans the built image for OS package and Python dependency vulnerabilities. Fails on CRITICAL and HIGH severity only. MEDIUM/LOW reported as info.

## Severity gating

All scans fail the workflow on findings (CRITICAL/HIGH for trivy, any finding for the rest). Main branch goes red if vulnerabilities exist — visible before deployment.

## File changes

**New:**
- `.github/workflows/security.yml` — workflow with both jobs

**Modified:**
- `pyproject.toml` — add `pip-audit`, `bandit`, `detect-secrets` to dev deps; add `[tool.bandit]` config

## Approach

Two-job workflow (selected): code scans and infra scans run in parallel. Logical grouping, 2 status checks, fast execution.

Rejected:
- One job per scan — 4 status checks clutters PR
- Single sequential job — slower, one failure hides remaining results