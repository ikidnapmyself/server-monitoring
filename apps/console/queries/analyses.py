"""Read model for the analyses list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import describe_incidents
from apps.alerts.models import Incident
from apps.console.filters import AnalysisFilter
from apps.console.queries.incidents import SINCE_CHIPS, ChipGroup, Choice, chip_group
from apps.intelligence.describing import describe_analysis_run, provider_label
from apps.intelligence.models import AnalysisRun, AnalysisRunStatus, IntelligenceProvider
from config.describing import Action, Description

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    *((str(value), str(label), str(value)) for value, label in AnalysisRunStatus.choices),
    ("all", "All", ""),
)
STATUS_TONES = {"pending": "info", "running": "info", "succeeded": "ok", "failed": "warning"}


@dataclass(frozen=True)
class AnalysisListRow:
    pk: int
    what: Description
    incident: Action | None
    provider: str
    tokens: str
    created_at: datetime
    link: Action


@dataclass(frozen=True)
class AnalysisList:
    rows: tuple[AnalysisListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    providers: tuple[tuple[str, str], ...]
    provider: str
    kept: tuple[tuple[str, str], ...]
    notice: str
    empty: str
    url: str


def tokens(count: int | None) -> str:
    if not count:
        return ""
    return f"{count:,} token{'' if count == 1 else 's'}"


def provider_choices() -> tuple[tuple[str, str], ...]:
    """Every provider kind, the local analyzer, and any key a stored analysis names."""
    stored = AnalysisRun.objects.order_by().values_list("provider", flat=True).distinct()
    keys = {"local", *(key for key, _ in IntelligenceProvider.PROVIDER_CHOICES), *stored} - {""}
    return tuple(sorted(((key, provider_label(key)) for key in keys), key=lambda c: c[1]))


def _matching(criteria: AnalysisFilter):
    runs = AnalysisRun.objects.all()
    if criteria.status:
        runs = runs.filter(status=criteria.status)
    if criteria.provider:
        runs = runs.filter(provider=criteria.provider)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        runs = runs.filter(created_at__gte=cutoff)
    return runs


def _status_counts(criteria: AnalysisFilter) -> dict[str, int]:
    return _matching(replace(criteria, status="")).aggregate(
        **{key: Count("pk", filter=Q(status=value)) for key, _, value in STATUS_CHIPS if value},
        all=Count("pk"),
    )


def _row(run: AnalysisRun, incidents: dict[int, Description]) -> AnalysisListRow:
    what = describe_analysis_run(run)
    incident = incidents.get(run.incident_id) if run.incident_id else None
    return AnalysisListRow(
        pk=run.pk,
        what=what,
        incident=(
            Action(incident.headline, "view_incident", {"pk": run.incident_id})
            if incident
            else None
        ),
        provider=provider_label(run.provider),
        tokens=tokens(run.total_tokens),
        created_at=run.created_at,
        link=Action(what.headline, "view_analysis", {"pk": run.pk}),
    )


def build_analysis_list(criteria: AnalysisFilter) -> AnalysisList:
    base = reverse("console:analyses")
    runs = _matching(criteria)
    total = runs.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        start = (page - 1) * PAGE_SIZE
        found = list(runs.order_by("-created_at", "-pk")[start : start + PAGE_SIZE])
        incidents = describe_incidents(
            Incident.objects.filter(pk__in={run.incident_id for run in found if run.incident_id})
        )
        rows, empty = tuple(_row(run, incidents) for run in found), ""
    elif not AnalysisRun.objects.exists():
        rows, empty = (), "none"
    else:
        rows, empty = (), "no_match"

    groups = (
        chip_group(
            "status", "Status", STATUS_CHIPS, criteria, base, _status_counts(criteria), STATUS_TONES
        ),
        chip_group("since", "Since", SINCE_CHIPS, criteria, base),
    )
    return AnalysisList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=groups,
        providers=provider_choices(),
        provider=criteria.provider,
        kept=criteria.kept(),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
