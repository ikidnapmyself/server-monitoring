"""Pipeline runs and stage outcomes in words, from the status and error each row stored."""

import re
from dataclasses import dataclass

from apps.orchestration.models import PipelineRun
from config.describing import Action, Description, Fact

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


def describe_stage_outcome(
    stage: str, status: str, error_message: str, *, retryable: bool
) -> Description:
    name = _stage_name(stage)
    if status == "failed":
        fallback = f"{name} failed and will be retried" if retryable else f"{name} failed"
        return _failed(_PREFIX.get(stage, f"{name} failed"), fallback, error_message)
    if status == "skipped":
        detail = error_message.removeprefix(_SKIP_PREFIX)
        return Description(f"{name} skipped", "muted", detail=detail)
    plain = {
        "succeeded": (f"{name} finished", "ok"),
        "running": (f"{name} is running", "info"),
        "retrying": (f"{name} is retrying", "info"),
        "pending": (f"{name} is waiting", "muted"),
    }
    headline, tone = plain.get(status, (f"{name} {status.replace('_', ' ')}", "muted"))
    return Description(headline, tone)


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
    ids = (
        Fact("Run", run.run_id, copy=True),
        Fact("Trace", run.trace_id, copy=True),
        Fact("Status", run.status),
    )
    if run.status == "failed":
        return _failed("Run failed", "Run failed", run.last_error_message or "", ids)
    headline, tone = _RUN_PLAIN.get(run.status, (f"Run {run.status.replace('_', ' ')}", "muted"))
    return Description(headline, tone, facts=ids)
