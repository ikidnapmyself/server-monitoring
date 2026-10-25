"""An analyze stage outcome in words, from the AnalyzeResult snapshot the run stored.

Snapshots come from old rows too, so every field is read defensively and a describer never
raises on a shape it doesn't expect.
"""

import math
from typing import Any

from apps.intelligence.models import AnalysisRun, AnalysisRunStatus, IntelligenceProvider
from config.describing import Action, Description, Fact, format_percent

PROVIDER_LABELS = dict(IntelligenceProvider.PROVIDER_CHOICES)
LOCAL_ANALYZER = "Built-in local analyzer"
UNKNOWN_PROVIDER = "A provider this hub doesn't have"
RUNNING = "AI analysis is still running"
URGENT = ("critical", "high")

_EDIT_PROVIDER = Action("Check the provider's settings", intent="edit_provider")

FAILURES: dict[str, tuple[str, Action]] = {
    "auth": (
        "AI analysis failed: the provider rejected the API key",
        Action("Update the provider's API key", intent="edit_provider"),
    ),
    "rate_limit": (
        "AI analysis failed: the provider is rate limiting this hub",
        Action("Wait, or raise the plan's limit"),
    ),
    "timeout": (
        "AI analysis failed: the provider didn't answer in time",
        Action("Wait for the next run to try again"),
    ),
    "provider_down": (
        "AI analysis failed: the provider is unreachable",
        Action("Check the provider's status page"),
    ),
    "rejected": (
        "AI analysis failed: the provider refused the request",
        Action("Check the provider's model and settings", intent="edit_provider"),
    ),
    "unknown": ("AI analysis failed", _EDIT_PROVIDER),
}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _confidence(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    # isfinite raises OverflowError on an int too large for a float.
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return format_percent(max(0, min(1, value)) * 100)


def analysis_failed(snapshot: Any) -> bool:
    """Whether the provider gave no real answer, so its summary and advice aren't its own."""
    if not isinstance(snapshot, dict):
        return False
    return snapshot.get("fallback_used") is True or bool(snapshot.get("failure"))


def describe_analysis(snapshot: Any) -> Description:
    if not isinstance(snapshot, dict):
        snapshot = {}
    failure = _text(snapshot.get("failure"))
    model_info = snapshot.get("model_info")
    provider = _text(model_info.get("provider")) if isinstance(model_info, dict) else ""
    facts = (Fact("Provider", provider),) if provider else ()

    if analysis_failed(snapshot):
        headline, action = FAILURES.get(failure, FAILURES["unknown"])
        error = _text(snapshot.get("error_detail"))
        if error:
            facts = (*facts, Fact("Error", error))
        return Description(headline, "warning", action=action, facts=facts)

    summary = _text(snapshot.get("summary")).strip()
    if not summary:
        return Description("AI analysis found nothing to add", "muted")
    # A snapshot without a failure key predates real confidence: the executor invented it.
    confidence = _confidence(snapshot.get("confidence")) if "failure" in snapshot else ""
    if confidence:
        facts = (Fact("Confidence", confidence), *facts)
    return Description(summary, "info", detail=_text(snapshot.get("probable_cause")), facts=facts)


def provider_label(key: str) -> str:
    """A provider key in words. ``local`` is the fallback analyzer, never a provider row."""
    if key == "local":
        return LOCAL_ANALYZER
    return str(PROVIDER_LABELS.get(key, UNKNOWN_PROVIDER))


def describe_analysis_run(run: AnalysisRun) -> Description:
    label = provider_label(run.provider)
    if run.status == AnalysisRunStatus.FAILED:
        headline, action = FAILURES["unknown"]
        error = (Fact("Error", run.error_message),) if run.error_message else ()
        return Description(
            headline, "warning", action=action, facts=(Fact("Provider", label), *error)
        )
    if run.status != AnalysisRunStatus.SUCCEEDED:
        return Description(RUNNING, "muted", facts=(Fact("Provider", label),))
    return describe_analysis(
        {
            "summary": run.summary,
            "probable_cause": run.probable_cause,
            "confidence": run.confidence,
            "fallback_used": run.fallback_used,
            "failure": "",
            "model_info": {"provider": label},
        }
    )


def describe_recommendations(items: Any) -> tuple[Description, ...]:
    """Stored recommendations as descriptions. Anything without a title is skipped."""
    found = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title")).strip()
        if not title:
            continue
        tone = "warning" if item.get("priority") in URGENT else "info"
        found.append(Description(title, tone, detail=_text(item.get("description")).strip()))
    return tuple(found)
