import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.describing import describe_incident
from apps.alerts.models import Incident
from apps.console.filters import AnalysisFilter
from apps.console.queries import analyses as analysis_queries
from apps.console.queries.analyses import build_analysis_list, provider_choices, tokens
from apps.console.queries.analysis import build_analysis_detail, tokens_in_words
from apps.intelligence.describing import (
    LOCAL_ANALYZER,
    UNKNOWN_PROVIDER,
    describe_analysis_run,
    describe_recommendations,
)
from apps.intelligence.models import AnalysisRun
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db


def _analysis(status="succeeded", provider="openai", **fields):
    defaults = {"trace_id": "t-1", "pipeline_run_id": "r-1", "summary": "Disk filled by logs"}
    return AnalysisRun.objects.create(status=status, provider=provider, **(defaults | fields))


def _aged(run, hours):
    AnalysisRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=hours))
    run.refresh_from_db()
    return run


def _group(listing, name):
    return next(g for g in listing.groups if g.name == name)


def test_rows_are_newest_first_and_described():
    incident = Incident.objects.create(severity="critical", status="open", subject="disk")
    old = _aged(_analysis(incident=incident, total_tokens=1234), 3)
    new = _aged(_analysis("failed", "retired_one"), 1)

    rows = build_analysis_list(AnalysisFilter()).rows

    assert [row.pk for row in rows] == [new.pk, old.pk]
    assert rows[0].what == describe_analysis_run(new)
    assert rows[0].link == Action("AI analysis failed", "view_analysis", {"pk": new.pk})
    assert (rows[0].incident, rows[0].provider, rows[0].tokens) == (
        None,
        UNKNOWN_PROVIDER,
        "",
    )
    assert rows[1].incident == Action(
        describe_incident(incident).headline, "view_incident", {"pk": incident.pk}
    )
    assert (rows[1].provider, rows[1].tokens) == ("OpenAI", "1,234 tokens")
    assert rows[1].created_at == old.created_at


def test_tokens():
    assert tokens(None) == tokens(0) == ""
    assert tokens(1) == "1 token"
    assert tokens(1500) == "1,500 tokens"


def test_each_filter_narrows_the_list():
    done = _analysis()
    failed = _analysis("failed", "claude")
    running = _analysis("running", "local")
    old = _aged(_analysis("pending"), 48)

    def picked(**fields):
        return {row.pk for row in build_analysis_list(AnalysisFilter(**fields)).rows}

    assert picked() == {done.pk, failed.pk, running.pk, old.pk}
    assert picked(status="failed") == {failed.pk}
    assert picked(status="pending") == {old.pk}
    assert picked(provider="local") == {running.pk}
    assert picked(since="24h") == {done.pk, failed.pk, running.pk}


def test_status_chips_count_the_other_filters_and_carry_tones():
    _analysis()
    _analysis("failed")
    _analysis("failed", "claude")

    listing = build_analysis_list(AnalysisFilter(provider="openai"))

    chips = {chip.key: chip for chip in _group(listing, "status").chips}
    assert {key: chip.count for key, chip in chips.items()} == {
        "pending": 0,
        "running": 0,
        "succeeded": 1,
        "failed": 1,
        "all": 2,
    }
    assert {key: chip.tone for key, chip in chips.items()} == {
        "pending": "info",
        "running": "info",
        "succeeded": "ok",
        "failed": "warning",
        "all": "",
    }
    assert chips["failed"].url == "/console/analyses/?status=failed&provider=openai"
    assert listing.kept == ()
    assert listing.provider == "openai"


def test_provider_choices_cover_known_local_and_stored_keys():
    _analysis(provider="retired_one")

    choices = dict(provider_choices())

    assert choices["local"] == LOCAL_ANALYZER
    assert choices["openai"] == "OpenAI"
    assert choices["retired_one"] == UNKNOWN_PROVIDER
    labels = [label for _, label in provider_choices()]
    assert labels == sorted(labels)


def test_empty_notice_and_pages(monkeypatch):
    assert build_analysis_list(AnalysisFilter()).empty == "none"
    _analysis()
    assert build_analysis_list(AnalysisFilter(status="failed")).empty == "no_match"
    listing = build_analysis_list(AnalysisFilter(status="succeeded", dropped=("provider",)))
    assert listing.notice == "Ignored invalid filter: provider."
    assert listing.kept == (("status", "succeeded"),)

    monkeypatch.setattr(analysis_queries, "PAGE_SIZE", 1)
    _analysis()
    first = build_analysis_list(AnalysisFilter())
    last = build_analysis_list(AnalysisFilter(page=5))
    assert (first.pages, first.prev_url, first.next_url) == (2, "", "/console/analyses/?page=2")
    assert (last.page, last.prev_url, last.next_url) == (2, "/console/analyses/", "")


def test_a_missing_analysis_is_none():
    assert build_analysis_detail(404) is None


def test_the_detail_keeps_config_and_refs_in_details():
    incident = Incident.objects.create(severity="critical", status="open", subject="disk")
    items = [{"title": "Rotate logs", "priority": "high"}, "junk"]
    run = _analysis(
        incident=incident,
        provider_config={"model": "gpt-4o", "api_key": "[REDACTED]"},
        model_name="gpt-4o",
        input_summary="disk at 97%",
        checker_output_ref="ref-1",
        recommendations=items,
        prompt_tokens=1200,
        completion_tokens=300,
        duration_ms=2500,
    )

    detail = build_analysis_detail(run.pk)

    assert detail.what.headline == "Disk filled by logs"
    assert detail.what.facts[-6:] == (
        Fact("Status", "succeeded"),
        Fact("Model", "gpt-4o"),
        Fact("Input", "disk at 97%"),
        Fact("Checker output", "ref-1"),
        Fact("Trace ID", "t-1", copy=True),
        Fact("Pipeline run ID", "r-1", copy=True),
    )
    assert json.loads(detail.raw) == {"model": "gpt-4o", "api_key": "[REDACTED]"}
    assert detail.recommendations == describe_recommendations(items)
    assert detail.incident.pk == incident.pk
    assert (detail.provider, detail.tokens, detail.took) == ("OpenAI", "1,200 in, 300 out", "2s")


def test_a_bare_detail_records_nothing_it_lacks():
    run = _analysis("pending", "local", trace_id="", pipeline_run_id="")

    detail = build_analysis_detail(run.pk)

    assert detail.incident is None
    assert (detail.tokens, detail.took, detail.recommendations) == ("", "", ())
    assert detail.provider == LOCAL_ANALYZER
    assert [fact.label for fact in detail.what.facts] == ["Provider", "Status"]


@pytest.mark.parametrize(
    ("prompt", "completion", "text"),
    [(None, None, ""), (10, None, "10 in, 0 out"), (None, 5, "0 in, 5 out")],
)
def test_tokens_in_words(prompt, completion, text):
    assert tokens_in_words(prompt, completion) == text
