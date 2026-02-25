# Intelligence

The **Intelligence** app provides local system analysis and generates actionable recommendations based on incidents and system state.

> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (ANALYZE stage).

## Features

### Local Recommendation Provider

The `LocalRecommendationProvider` analyzes system state and incidents to generate recommendations:

- **Memory Analysis**: Identifies top memory-consuming processes
- **Disk Analysis**: Finds large files, directories, and old logs that can be cleaned up
- **CPU Analysis**: Identifies high CPU-consuming processes
- **Incident-Based**: Automatically detects incident type and provides relevant recommendations

## Quick Start

### Management Command: `get_recommendations`

All flags can be passed after aliases too (e.g., `sm-get-recommendations --memory`).

```bash
# Default: get general recommendations
uv run python manage.py get_recommendations
```

#### Analysis modes

```bash
# Memory analysis: top processes by memory usage
uv run python manage.py get_recommendations --memory

# Disk analysis: large files, old logs, cleanup candidates
uv run python manage.py get_recommendations --disk

# Disk analysis for a specific path
uv run python manage.py get_recommendations --disk --path /var/log
uv run python manage.py get_recommendations --disk --path /home

# All analysis (memory + disk combined)
uv run python manage.py get_recommendations --all
```

#### Incident-based analysis

```bash
# Analyze a specific incident (auto-detects type from title/description)
uv run python manage.py get_recommendations --incident-id 1
uv run python manage.py get_recommendations --incident-id 42

# Incident analysis with specific provider
uv run python manage.py get_recommendations --incident-id 1 --provider local
```

#### Provider selection

```bash
# List available providers
uv run python manage.py get_recommendations --list-providers

# Use a specific provider
uv run python manage.py get_recommendations --provider local
```

#### Tuning parameters

```bash
# Show top 5 processes (default: 10)
uv run python manage.py get_recommendations --top-n 5

# Show top 20 processes
uv run python manage.py get_recommendations --memory --top-n 20

# Lower threshold for "large" files (default: 100 MB)
uv run python manage.py get_recommendations --disk --threshold-mb 50

# Higher threshold
uv run python manage.py get_recommendations --disk --threshold-mb 500

# Detect files older than 7 days (default: 30)
uv run python manage.py get_recommendations --disk --old-days 7

# Detect files older than 90 days
uv run python manage.py get_recommendations --disk --old-days 90
```

#### JSON output

```bash
uv run python manage.py get_recommendations --json
uv run python manage.py get_recommendations --memory --json
uv run python manage.py get_recommendations --all --json
```

#### Combined examples

```bash
# Full analysis with tuned parameters + JSON
uv run python manage.py get_recommendations --all \
  --top-n 15 --threshold-mb 50 --old-days 14 --json

# Disk analysis for /var/log with low threshold
uv run python manage.py get_recommendations --disk \
  --path /var/log --threshold-mb 10 --old-days 7

# Memory analysis with top 5 + JSON
uv run python manage.py get_recommendations --memory --top-n 5 --json

# Incident analysis with custom provider + JSON
uv run python manage.py get_recommendations --incident-id 1 --provider local --json
```

#### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--memory` | flag | — | Get memory-specific recommendations |
| `--disk` | flag | — | Get disk-specific recommendations |
| `--all` | flag | — | Get all recommendations (memory + disk) |
| `--path` | str | `/` | Path to analyze for disk recommendations |
| `--incident-id` | int | — | Analyze a specific incident by ID |
| `--provider` | str | `local` | Intelligence provider to use |
| `--list-providers` | flag | — | List available providers and exit |
| `--top-n` | int | `10` | Number of top processes to report |
| `--threshold-mb` | float | `100.0` | Minimum file size in MB for "large" |
| `--old-days` | int | `30` | Age in days for old file detection |
| `--json` | flag | — | Output as JSON |

### HTTP API

Start the Django server:

```bash
uv run python manage.py runserver
```

#### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/intelligence/health/` | GET | Health check |
| `/intelligence/providers/` | GET | List available providers |
| `/intelligence/recommendations/` | GET/POST | Get recommendations |
| `/intelligence/memory/` | GET | Memory-specific analysis |
| `/intelligence/disk/` | GET | Disk-specific analysis |

#### Examples

```bash
# Get general recommendations
curl http://localhost:8000/intelligence/recommendations/

# Get recommendations for an incident
curl http://localhost:8000/intelligence/recommendations/?incident_id=1

# Get memory analysis
curl http://localhost:8000/intelligence/memory/?top_n=5

# Get disk analysis
curl "http://localhost:8000/intelligence/disk/?path=/var/log&threshold_mb=50&old_days=7"

# POST with custom configuration
curl -X POST http://localhost:8000/intelligence/recommendations/ \
  -H "Content-Type: application/json" \
  -d '{"provider": "local", "config": {"top_n_processes": 5}}'
```

## Programmatic Usage

```python
from apps.intelligence.providers import get_provider, get_local_recommendations

# Quick access
recommendations = get_local_recommendations()

# With an incident
from apps.alerts.models import Incident
incident = Incident.objects.get(id=1)
recommendations = get_local_recommendations(incident)

# Custom configuration
provider = get_provider(
    "local",
    top_n_processes=5,
    large_file_threshold_mb=50.0,
    old_file_days=7,
)
recommendations = provider.get_recommendations()

# Specific analysis
memory_recs = provider._get_memory_recommendations()
disk_recs = provider._get_disk_recommendations("/var/log")
```

## Recommendation Types

### Memory Recommendations

When memory usage is high, the provider returns:

- List of top memory-consuming processes (PID, name, memory %, MB used)
- Suggested actions (restart services, check for leaks, increase RAM)

### Disk Recommendations

For disk space issues, the provider returns:

- **Large Files/Directories**: Items exceeding the threshold (default 100MB)
- **Old Logs**: Files older than the configured age (default 30 days)
- Suggested actions (review files, run ncdu, configure logrotate)

### CPU Recommendations

When CPU usage is high:

- List of top CPU-consuming processes
- Load average information
- Suggested actions (investigate processes, check for runaway jobs)

## Provider Architecture

The intelligence system uses a provider-based architecture with DB-driven selection:

```
apps/intelligence/
├── providers/
│   ├── __init__.py      # Registry, get_active_provider, exports
│   ├── base.py          # BaseProvider interface
│   ├── ai_base.py       # BaseAIProvider (shared LLM logic)
│   ├── local.py         # LocalRecommendationProvider (fallback/default)
│   ├── openai.py        # OpenAI (GPT models)
│   ├── claude.py        # Claude (Anthropic)
│   ├── gemini.py        # Gemini (Google)
│   ├── copilot.py       # GitHub Copilot
│   ├── grok.py          # Grok (xAI)
│   ├── ollama.py        # Ollama (local LLM)
│   └── mistral.py       # Mistral AI
├── models.py            # IntelligenceProvider (DB config)
├── views.py             # HTTP API endpoints
├── urls.py              # URL routing
└── management/
    └── commands/
        └── get_recommendations.py
```

### Available Providers

| Provider | SDK | Config Keys |
|----------|-----|-------------|
| `local` | None (built-in) | `top_n_processes`, `large_file_threshold_mb`, `old_file_days` |
| `openai` | `openai` | `api_key`, `model` (default: gpt-4o-mini), `max_tokens` |
| `claude` | `anthropic` | `api_key`, `model` (default: claude-sonnet-4-20250514), `max_tokens` |
| `gemini` | `google-genai` | `api_key`, `model` (default: gemini-2.0-flash), `max_tokens` |
| `copilot` | `openai` | `api_key`, `model` (default: gpt-4o), `base_url`, `max_tokens` |
| `grok` | `openai` | `api_key`, `model` (default: grok-3-mini), `base_url`, `max_tokens` |
| `ollama` | `ollama` | `host` (default: http://localhost:11434), `model` (default: llama3.1), `max_tokens` |
| `mistral` | `mistralai` | `api_key`, `model` (default: mistral-small-latest), `max_tokens` |

### DB-Driven Provider Selection

Providers are configured via Django Admin (`IntelligenceProvider` model):

1. Go to Admin > Intelligence > Intelligence Providers
2. Create a provider with type, name, and config (JSON with api_key, model, etc.)
3. Set `is_active=True` — only one can be active at a time
4. The orchestrator calls `get_active_provider()` which queries the DB
5. If no active provider exists, falls back to `local`

```python
from apps.intelligence.providers import get_active_provider

# Returns the DB-configured active provider, or local as fallback
provider = get_active_provider()
recommendations = provider.analyze(incident)
```

### Extending with New Providers

1. Create a new provider extending `BaseAIProvider`:

```python
from apps.intelligence.providers.ai_base import BaseAIProvider

class MyProvider(BaseAIProvider):
    name = "myprovider"
    description = "My custom LLM provider"
    default_model = "my-model-v1"

    def _call_api(self, prompt: str) -> str:
        # Call your LLM API and return the response text
        ...
```

2. Register it in `apps/intelligence/providers/__init__.py` with a try/except guard.
3. Add the provider type to `IntelligenceProvider.PROVIDER_CHOICES` in `models.py`.

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `top_n_processes` | 10 | Number of top processes to report |
| `large_file_threshold_mb` | 100.0 | Minimum size to consider a file "large" |
| `old_file_days` | 30 | Age after which files are considered old |
| `scan_paths` | `/var/log`, `/tmp`, etc. | Paths to scan for old files |

## Tests

Run the tests:

```bash
uv run pytest apps/intelligence/_tests/ -v
```

## Integration with Alerts

The intelligence system automatically detects incident types from the alerts app:

```python
from apps.alerts.models import Incident
from apps.intelligence.providers import get_local_recommendations

# Create an incident
incident = Incident.objects.create(
    title="High Memory Usage Alert",
    description="Memory usage exceeded 90%",
)

# Get targeted recommendations
recommendations = get_local_recommendations(incident)
# Returns memory-specific recommendations
```

The provider parses incident titles and descriptions to detect:
- **Memory incidents**: Keywords like "memory", "ram", "oom", "swap"
- **Disk incidents**: Keywords like "disk", "storage", "space", "filesystem"
- **CPU incidents**: Keywords like "cpu", "load", "processor"
