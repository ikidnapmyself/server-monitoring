# Architecture Documentation Restructure Design

## Goal

Create a centralized Architecture doc covering all entry points and pipeline stages. Clean up app READMEs to remove duplication with root docs. Standardize `docs/` file naming to Capitalized.

## Audience

Both new developers (understanding how everything connects) and ops/SRE teams (entry points, configs, failure modes).

## Approach

Architecture-first: create `docs/Architecture.md` as the anchor, then clean app READMEs and root README to fit around it.

## New File: `docs/Architecture.md`

### Structure

1. **Overview** — One-paragraph system description, tech stack
2. **Pipeline Stages** — INGEST → CHECK → ANALYZE → NOTIFY, each stage's owning app, input/output contract, text diagram
3. **Entry Points**
   - Management Commands (8): check_health, run_check, check_and_alert, get_recommendations, list_notify_drivers, test_notify, run_pipeline, monitor_pipeline
   - HTTP Endpoints: alerts webhooks, intelligence API, notify API, orchestration API
   - Celery Tasks: alerts.tasks, orchestration.tasks
   - Django Admin: per-app model admin
4. **Orchestration** — Merged from `orchestration-pipelines.md`: hardcoded vs definition-based pipelines, state machine, correlation IDs, node types, example configs
5. **Data Models** — Key models per app with relationships (Alert→Incident, CheckRun, AnalysisRun, PipelineRun→StageExecution, NotificationChannel)
6. **Configuration** — Key env vars, skip controls, provider settings

### Content source

Pipeline sections merge content from `docs/orchestration-pipelines.md`, which gets deleted after merge.

## App README Cleanup

**Remove from each `apps/*/README.md`:**
- Pipeline overview paragraphs (now in Architecture.md)
- General project setup/install references
- Cross-app architecture descriptions

**Keep/enrich in each `apps/*/README.md`:**
- Models with field-level detail
- Drivers/providers/checkers specific to that app
- Configuration options specific to that app
- Management command usage with full examples
- HTTP endpoint request/response examples
- Extension guides (how to add a new driver/checker/provider)
- Django Admin features specific to that app

## Root README.md Trim

- Keep: badges, 1-paragraph intro, requirements, install quickstart, dev commands
- Remove: detailed pipeline description, per-app command listings
- Add: links to `docs/Architecture.md` and each `apps/*/README.md`

## File Operations

| Action | File |
|--------|------|
| Create | `docs/Architecture.md` |
| Delete | `docs/orchestration-pipelines.md` |
| Edit | `apps/alerts/README.md` |
| Edit | `apps/checkers/README.md` |
| Edit | `apps/intelligence/README.md` |
| Edit | `apps/notify/README.md` |
| Edit | `apps/orchestration/README.md` |
| Edit | `README.md` |
| Edit | `CLAUDE.md` |
