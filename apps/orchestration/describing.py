"""Pipeline runs and stage outcomes in words, from the status and error each row stored."""

import re
from dataclasses import dataclass, replace
from typing import Any

from apps.orchestration.models import PipelineRun
from config.describing import Action, Description, Fact, format_duration, or_action

STAGE_NAMES = {"ingest": "Ingest", "check": "Diagnose", "analyze": "Analyze", "notify": "Notify"}
_CODE = re.compile(r"^([a-z]+(?:_[a-z]+)+): ")


@dataclass(frozen=True)
class Reason:
    headline: str
    action: Action


REASONS: dict[str, Reason] = {
    "no_route": Reason(
        "no route matches this incident",
        Action("Add a route or widen the catch-all", intent="edit_route"),
    ),
    "no_channel": Reason(
        "the matched route has no active channel",
        Action("Give the route an active channel", intent="edit_route"),
    ),
    "no_driver": Reason(
        "the channel uses a driver this hub doesn't have",
        Action("Change the channel's driver", intent="edit_channel"),
    ),
}

_PREFIX = {"notify": "Not delivered", "check": "Diagnose failed", "analyze": "Analyze failed"}
_OPEN_ERROR = Action("Open the run's error in Details")
_SKIP_PREFIX = "Skipped: "


def reason_code(message: str) -> str:
    found = _CODE.match(message or "")
    return found.group(1) if found else ""


def _stage_name(stage: str) -> str:
    return STAGE_NAMES.get(stage, stage.replace("_", " ").capitalize())


def _failed(prefix: str, fallback: str, message: str, ids: tuple[Fact, ...] = ()) -> Description:
    code = reason_code(message)
    reason = REASONS.get(code)
    if reason:
        detail = message[len(code) + 2 :]
        return Description(
            f"{prefix}: {reason.headline}",
            "critical",
            action=reason.action,
            facts=(Fact("Error", detail), *ids),
        )
    error = (Fact("Error", message),) if message else ()
    return Description(fallback, "critical", action=_OPEN_ERROR, facts=(*error, *ids))


def run_link(run_pk: int) -> Action:
    return Action("Open the run", intent="view_run", params={"pk": run_pk})


def describe_stage_outcome(
    stage: str,
    status: str,
    error_message: str,
    *,
    retryable: bool,
    duration_ms: float | None = None,
) -> Description:
    name = _stage_name(stage)
    if status == "failed":
        retry = (Fact("Retryable", "yes" if retryable else "no"),)
        return _failed(_PREFIX.get(stage, f"{name} failed"), f"{name} failed", error_message, retry)
    if status == "skipped":
        detail = error_message.removeprefix(_SKIP_PREFIX)
        return Description(f"{name} skipped", "muted", detail=detail)
    took = f" in {format_duration(duration_ms / 1000)}" if duration_ms else ""
    plain = {
        "succeeded": (f"{name} finished{took}", "ok"),
        "running": (f"{name} is running", "info"),
        "retrying": (f"{name} is retrying", "info"),
        "pending": (f"{name} is waiting", "muted"),
    }
    headline, tone = plain.get(status, (f"{name} {status.replace('_', ' ')}", "muted"))
    return Description(headline, tone)


_DIAGNOSIS_PLAIN = {
    "never_ran": ("{} hasn't run yet", "muted"),
    "empty": ("{} finished with nothing to report", "warning"),
    "stalled": ("{} stalled", "warning"),
}


def describe_stage_diagnosis(entry: dict[str, Any]) -> Description:
    """One entry of ``apps.alerts.diagnosis.diagnose_incident`` in words."""
    stage = entry["stage"]
    if entry["status"] in _DIAGNOSIS_PLAIN:
        headline, tone = _DIAGNOSIS_PLAIN[entry["status"]]
        described = Description(headline.format(_stage_name(stage)), tone)
    else:
        described = describe_stage_outcome(
            stage,
            entry["execution_status"] or "",
            entry["error_message"],
            retryable=entry["retryable"],
        )
    if entry["runs"]:
        described = replace(described, facts=(*described.facts, Fact("Runs", entry["runs"])))
    if entry["run_pk"] is not None:
        described = or_action(described, run_link(entry["run_pk"]))
    return described


_RUN_PLAIN = {
    "notified": ("Run finished, notified", "ok"),
    "pending": ("Run queued", "muted"),
    "processing": ("Run in progress", "info"),
    "ingested": ("Run in progress, ingest done", "info"),
    "checked": ("Run in progress, diagnose done", "info"),
    "analyzed": ("Run in progress, analyze done", "info"),
    "retrying": ("Run will be retried", "info"),
    "skipped": ("Run skipped", "muted"),
}


def describe_run(run: PipelineRun) -> Description:
    return describe_run_fields(
        run_id=run.run_id,
        trace_id=run.trace_id,
        status=run.status,
        error_message=run.last_error_message or "",
    )


def describe_run_fields(
    *, run_id: str, trace_id: str, status: str, error_message: str
) -> Description:
    """``describe_run`` from the stored fields, for a reader that holds a row's values."""
    ids = (
        Fact("Run", run_id, copy=True),
        Fact("Trace", trace_id, copy=True),
        Fact("Status", status),
    )
    if status == "failed":
        return _failed("Run failed", "Run failed", error_message, ids)
    headline, tone = _RUN_PLAIN.get(status, (f"Run {status.replace('_', ' ')}", "muted"))
    return Description(headline, tone, facts=ids)
