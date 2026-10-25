"""Read model for one analysis's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass, replace
from datetime import datetime

from apps.console.queries.alert import IncidentCard, incident_card
from apps.intelligence.describing import (
    describe_analysis_run,
    describe_recommendations,
    provider_label,
)
from apps.intelligence.models import AnalysisRun
from config.describing import Description, Fact, format_duration

REFS = (
    ("Model", "model_name"),
    ("Input", "input_summary"),
    ("Checker output", "checker_output_ref"),
    ("Trace ID", "trace_id"),
    ("Pipeline run ID", "pipeline_run_id"),
)


@dataclass(frozen=True)
class AnalysisDetail:
    pk: int
    what: Description
    recommendations: tuple[Description, ...]
    incident: IncidentCard | None
    provider: str
    tokens: str
    took: str
    created_at: datetime
    raw: str


def tokens_in_words(prompt: int | None, completion: int | None) -> str:
    if prompt is None and completion is None:
        return ""
    return f"{prompt or 0:,} in, {completion or 0:,} out"


def build_analysis_detail(pk: int) -> AnalysisDetail | None:
    run = AnalysisRun.objects.select_related("incident").filter(pk=pk).first()
    if run is None:
        return None
    what = describe_analysis_run(run)
    refs = tuple(
        Fact(label, getattr(run, field), copy=field.endswith("_id"))
        for label, field in REFS
        if getattr(run, field)
    )
    return AnalysisDetail(
        pk=run.pk,
        what=replace(what, facts=(*what.facts, Fact("Status", run.status), *refs)),
        recommendations=describe_recommendations(run.recommendations),
        incident=incident_card(run.incident),
        provider=provider_label(run.provider),
        tokens=tokens_in_words(run.prompt_tokens, run.completion_tokens),
        took=format_duration(run.duration_ms / 1000) if run.duration_ms else "",
        created_at=run.created_at,
        raw=json.dumps(run.provider_config, indent=2, sort_keys=True, default=str),
    )
