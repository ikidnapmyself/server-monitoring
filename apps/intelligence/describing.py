"""An analyze stage outcome in words, from the AnalyzeResult snapshot the run stored.

Snapshots come from old rows too, so every field is read defensively and a describer never
raises on a shape it doesn't expect.
"""

import math
from typing import Any

from config.describing import Action, Description, Fact, format_percent

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


def describe_analysis(snapshot: Any) -> Description:
    if not isinstance(snapshot, dict):
        snapshot = {}
    failure = _text(snapshot.get("failure"))
    model_info = snapshot.get("model_info")
    provider = _text(model_info.get("provider")) if isinstance(model_info, dict) else ""
    facts = (Fact("Provider", provider),) if provider else ()

    if snapshot.get("fallback_used") is True or snapshot.get("failure"):
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
