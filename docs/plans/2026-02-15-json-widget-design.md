# JSON Widget Design

**Goal:** Beautify all JSONField displays in Django admin using `django-json-widget` for editable fields and a `prettify_json` helper for readonly fields.

---

## Approach

Use `django-json-widget` to replace the default textarea with a collapsible, syntax-highlighted JSON editor. For readonly JSON fields, use a `format_html` helper that renders pretty-printed JSON in a styled `<pre>` block.

## Scope

### Editable JSON fields (get widget)

| App | Model | Field |
|-----|-------|-------|
| `orchestration` | `PipelineDefinition` | `config`, `tags` |
| `orchestration` | `StageExecution` | `output_snapshot` |
| `notify` | `NotificationChannel` | `config` |

### Readonly JSON fields (get `prettify_json` display)

| App | Model | Field |
|-----|-------|-------|
| `alerts` | `Alert` | `labels`, `annotations`, `raw_payload` |
| `alerts` | `Incident` | `metadata` |
| `alerts` | `AlertHistory` | `details` |
| `checkers` | `CheckRun` | `metrics` |
| `intelligence` | `AnalysisRun` | `provider_config`, `recommendations` |

## Implementation

1. Add `django-json-widget` to `pyproject.toml` and install
2. Add `"django_json_widget"` to `INSTALLED_APPS` in `config/settings.py`
3. Create a shared `prettify_json` helper in a common location
4. Apply `JSONEditorWidget` via `formfield_overrides` on admins with editable JSON fields
5. Add `prettify_json` readonly display methods on admins with readonly JSON fields