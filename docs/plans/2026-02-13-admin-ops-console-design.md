# Admin Ops Console Design

**Date:** 2026-02-13
**Approach:** Enhanced Django Admin with Strategic Packages (Approach A)
**Audience:** Solo dev / operator
**Dependencies:** `django-object-actions` (per-object buttons); everything else is built-in Django

## 1. Custom Admin Dashboard Index

Replace the default Django admin index with a custom template showing system health at a glance.

**Panels:**
- **Active Incidents** — count + severity breakdown (critical/warning/info), links to filtered list
- **Pipeline Health (24h)** — total runs, success/fail/retrying counts, in-flight count
- **Recent Check Runs** — last 10 check runs with status badges, grouped by checker name
- **Failed Pipelines** — last 5 failed pipeline runs with error type and link to detail

**Implementation:**
- Subclass `AdminSite`, override `each_context()` to inject query data
- Override `index_template` to render the dashboard
- All queries use existing indexed fields (`status`, `created_at`, `severity`)
- Use `select_related('incident')` where needed

## 2. End-to-End Pipeline Tracing

Make it trivial to follow an alert through the full pipeline from any entry point.

**AlertAdmin:**
- Add "Pipeline Trace" readonly field showing: Alert -> Incident -> PipelineRun(s) -> StageExecutions
- Each element is a clickable admin link
- Add `trace_id` to search fields

**IncidentAdmin:**
- Enhance existing `PipelineRunInline` with stage execution count and last error preview per run
- Add "View Full Pipeline" link on each inline row

**PipelineRunAdmin:**
- Add "Pipeline Flow" readonly field at top of detail page
- Horizontal stage display: INGEST -> CHECK -> ANALYZE -> NOTIFY with colored status indicators
- Uses `format_html()`, no JS

**CheckRunAdmin / AnalysisRunAdmin:**
- Add "View Pipeline Run" link field navigating to parent PipelineRun via `trace_id`

**Implementation:** All custom readonly fields using `format_html()` and `reverse()` URL lookups. `trace_id` is already indexed everywhere.

## 3. Operational Actions

### Per-Object Actions (django-object-actions)

| Model | Action | Type | Behavior |
|-------|--------|------|----------|
| Incident | Acknowledge | Real | Calls `incident.acknowledge()`, sets `acknowledged_at` |
| Incident | Resolve | Real | Calls `incident.resolve()`, sets `resolved_at` |
| Incident | Close | Real | Calls `incident.close()`, sets `closed_at` |
| PipelineRun | Mark for Retry | State-only | Sets `status=RETRYING`, increments `total_attempts` |
| PipelineRun | Mark Failed | State-only | Sets `status=FAILED` with reason |

### Bulk Actions (built-in Django)

| Model | Action | Behavior |
|-------|--------|----------|
| Incident | Acknowledge selected | Bulk `acknowledge()` on queryset |
| Incident | Resolve selected | Bulk `resolve()` on queryset |
| Alert | Resolve selected alerts | Bulk set `status=RESOLVED` |
| PipelineRun | Mark selected for retry | Bulk set `status=RETRYING` |

**Safety:** Dangerous actions (retry, mark failed) show confirmation intermediate page. State-only actions don't trigger pipeline execution — they mark records for the retry mechanism to pick up.

## 4. Aggregation Views

Aggregation panels rendered on the dashboard (no separate pages).

| Panel | Query | Display |
|-------|-------|---------|
| Pipeline success rate (24h) | `PipelineRun` grouped by status | "87% success (142/163)" with colored bar |
| Top failing checkers (7d) | `CheckRun` WARNING/CRITICAL grouped by `checker_name`, top 5 | Ranked table |
| Error types (7d) | `PipelineRun` FAILED grouped by `last_error_type`, top 5 | Ranked table |
| Intelligence provider usage (7d) | `AnalysisRun` grouped by `provider` with `Count`/`Sum(total_tokens)` | Table with token counts |

**Implementation:** Computed in `AdminSite.each_context()`, rendered in dashboard template. Pure ORM aggregation on indexed fields. Styled HTML tables + percentage bar via `format_html()`. No charting library (Chart.js can be layered on later if needed).

## 5. Performance & Query Optimization

### get_queryset() Overrides

| ModelAdmin | select_related | prefetch_related |
|------------|---------------|-----------------|
| AlertAdmin | `incident` | — |
| IncidentAdmin | — | `alerts`, `pipeline_runs` |
| PipelineRunAdmin | `incident` | `stage_executions` |
| StageExecutionAdmin | `pipeline_run` | — |
| AnalysisRunAdmin | `incident` | — |

### Dashboard Queries
- Use `.only()` to fetch minimal columns
- Aggregations use DB-level `Count`/`Sum`, not Python-side
- No N+1 queries: every `list_display` method accessing related objects uses pre-fetched data