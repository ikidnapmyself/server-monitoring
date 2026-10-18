"""The re-evaluate preview every confirm page shares. Returns dataclasses, never querysets."""

from dataclasses import dataclass

from apps.alerts.describing import (
    REOPEN_WARNING,
    describe_reeval_consequences,
    describe_reeval_report,
)
from apps.alerts.reeval_existing import ReevalScope, preview_reeval, scope_digest
from config.describing import Description


@dataclass(frozen=True)
class ReevalPreview:
    title: str
    rows: tuple[Description, ...]
    notes: tuple[str, ...]
    warning: str
    empty: str
    can_apply: bool
    digest: str
    back_url: str
    back_text: str
    url: str


def reeval_preview(
    scope: ReevalScope,
    *,
    title: str,
    back_url: str,
    back_text: str,
    url: str,
    named: bool = False,
    empty: str = "",
) -> ReevalPreview:
    # Before the preview: if the scope moves in between, the apply refuses rather than
    # writing a change the operator never saw.
    digest = scope_digest(scope)
    report = preview_reeval(scope)
    rows = describe_reeval_report(report, named=named)
    return ReevalPreview(
        title=title,
        rows=rows,
        notes=describe_reeval_consequences(report),
        warning=REOPEN_WARNING if report.reopened_count else "",
        empty="" if rows else empty,
        can_apply=bool(report.changes) and report.node is not None,
        digest=digest,
        back_url=back_url,
        back_text=back_text,
        url=url,
    )
