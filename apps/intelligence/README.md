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

### Management Command

```bash
# Get recommendations based on current system state
uv run python manage.py get_recommendations

# Get memory-specific recommendations
uv run python manage.py get_recommendations --memory

# Get disk recommendations for a specific path
uv run python manage.py get_recommendations --disk --path=/var/log

# Analyze a specific incident
uv run python manage.py get_recommendations --incident-id=1

# Get all recommendations (memory + disk)
uv run python manage.py get_recommendations --all

# Output as JSON
uv run python manage.py get_recommendations --json

# List available providers
uv run python manage.py get_recommendations --list-providers

# Use a specific provider
uv run python manage.py get_recommendations --provider local

# Customize analysis parameters
uv run python manage.py get_recommendations --top-n 5          # Top N processes to show
uv run python manage.py get_recommendations --threshold-mb 50  # Min file size for "large"
uv run python manage.py get_recommendations --old-days 7       # Age for old file detection
```

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

The intelligence system uses a provider-based architecture:

```
apps/intelligence/
├── providers/
│   ├── __init__.py      # Registry and exports
│   ├── base.py          # BaseProvider interface
│   └── local.py         # LocalRecommendationProvider
├── views.py             # HTTP API endpoints
├── urls.py              # URL routing
└── management/
    └── commands/
        └── get_recommendations.py
```

### Extending with New Providers

1. Create a new provider in `apps/intelligence/providers/`:

```python
from apps.intelligence.providers.base import BaseProvider, Recommendation

class MyCustomProvider(BaseProvider):
    name = "my_custom"
    description = "My custom intelligence provider"

    def analyze(self, incident=None):
        # Your analysis logic
        return []

    def get_recommendations(self):
        # Your recommendations logic
        return []
```

2. Register it in `apps/intelligence/providers/__init__.py`:

```python
from apps.intelligence.providers.my_custom import MyCustomProvider

PROVIDERS["my_custom"] = MyCustomProvider
```

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
uv run pytest apps/intelligence/tests.py -v
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
