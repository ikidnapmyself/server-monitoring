"""Reading checker metrics back out of an alert's annotations.

A pure JSON helper with no Django or scoring dependencies, so any hub-side module
that needs a checker's metrics can import it without pulling in severity scoring.
``apps.alerts.reevaluation`` re-exports ``parse_metrics`` for its existing callers.
"""

import json


def parse_metrics(annotations: dict | None) -> dict | None:
    """Parse the JSON `metrics` string stashed in an alert's annotations.

    Shared by ingest (`ParsedAlert`) and config-change re-eval (`Alert`).
    Returns the dict, or None when absent / unparseable / not a dict.
    """
    raw = (annotations or {}).get("metrics")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
