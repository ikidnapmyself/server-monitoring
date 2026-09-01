"""Tests for PreflightRun + PreflightCheck models."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.checkers.models import PreflightCheck, PreflightRun


@pytest.mark.django_db
def test_preflight_run_with_counts_and_children():
    run = PreflightRun.objects.create(
        instance_id="node-1",
        passed=3,
        warnings=1,
        errors=0,
        overall_status="warn",
        triggered_by="cli",
    )
    PreflightCheck.objects.create(run=run, level="ok", message="Python OK", hint="")
    PreflightCheck.objects.create(run=run, level="warn", message="DEBUG on", hint="Disable DEBUG")

    assert run.instance_id == "node-1"
    assert run.passed == 3
    assert run.warnings == 1
    assert run.errors == 0
    assert run.overall_status == "warn"
    assert run.triggered_by == "cli"
    assert run.checks.count() == 2

    child = run.checks.get(level="warn")
    assert child.message == "DEBUG on"
    assert child.hint == "Disable DEBUG"


@pytest.mark.django_db
def test_preflight_run_default_ordering_newest_first():
    now = timezone.now()
    # Written newest first, so this passes only if the order comes from
    # created_at and not from the order the rows were inserted in.
    newer = PreflightRun.objects.create(overall_status="error")
    older = PreflightRun.objects.create(overall_status="ok")
    # created_at is auto_now_add, so a value passed to create() is discarded;
    # stamping it afterwards is what actually spaces the rows out in time.
    PreflightRun.objects.filter(pk=newer.pk).update(created_at=now)
    PreflightRun.objects.filter(pk=older.pk).update(created_at=now - timedelta(minutes=10))

    runs = list(PreflightRun.objects.all())
    assert runs[0] == newer
    assert runs[1] == older


@pytest.mark.django_db
def test_preflight_run_str():
    run = PreflightRun.objects.create(overall_status="ok")
    assert "Preflight" in str(run)
    assert "ok" in str(run)


@pytest.mark.django_db
def test_preflight_check_str():
    run = PreflightRun.objects.create(overall_status="warn")
    check = PreflightCheck.objects.create(run=run, level="warn", message="DEBUG on")
    assert str(check) == "[warn] DEBUG on"
