# Sample alert payloads

Ready-made webhook payloads (one per source format) for feeding the pipeline or
testing the alert drivers. These are **input fixtures**, not pipeline definitions.

```bash
# POST one to the webhook (works from anywhere)
curl -X POST http://localhost:8000/alerts/webhook/grafana/ \
  -H 'Content-Type: application/json' \
  --data @apps/orchestration/sample_payloads/grafana-alert.json

# Or feed one to run_pipeline --file. Note: --file goes through resolve_safe_path,
# which only allows absolute paths under the deployment roots (e.g. /opt in prod).
# From a dev checkout, copy the file to an allowed dir first, e.g.:
cp apps/orchestration/sample_payloads/alertmanager-alert.json /tmp/
uv run python manage.py run_pipeline --file /tmp/alertmanager-alert.json --source alertmanager
```

| File | Source format |
|------|---------------|
| `alertmanager-alert.json` | Prometheus Alertmanager |
| `grafana-alert.json` | Grafana |
| `pagerduty-alert.json` | PagerDuty Events API v2 |
