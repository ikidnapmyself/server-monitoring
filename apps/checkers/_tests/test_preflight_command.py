"""Tests for preflight command persistence."""

import pytest
from django.core.management import call_command

from apps.checkers.models import PreflightRun
from apps.checkers.preflight import CheckResult


@pytest.mark.django_db
def test_preflight_persists_by_default():
    call_command("preflight")
    assert PreflightRun.objects.count() == 1
    assert PreflightRun.objects.first().checks.exists()


@pytest.mark.django_db
def test_preflight_no_save_skips_persistence():
    call_command("preflight", "--no-save")
    assert PreflightRun.objects.count() == 0


@pytest.mark.django_db
def test_preflight_counts_match_results():
    call_command("preflight")
    run = PreflightRun.objects.first()
    assert run.passed + run.warnings + run.errors == run.checks.count()


@pytest.mark.django_db
def test_preflight_json_persists():
    call_command("preflight", "--json")
    assert PreflightRun.objects.count() == 1
    assert PreflightRun.objects.first().checks.exists()


@pytest.mark.django_db
def test_preflight_json_no_save_skips_persistence():
    call_command("preflight", "--json", "--no-save")
    assert PreflightRun.objects.count() == 0


@pytest.mark.django_db
def test_preflight_overall_status_error(monkeypatch):
    def fake_run_all(base_dir):
        return [
            CheckResult(level="ok", message="fine"),
            CheckResult(level="warn", message="hmm"),
            CheckResult(level="error", message="broken"),
        ]

    monkeypatch.setattr("apps.checkers.management.commands.preflight.run_all", fake_run_all)
    call_command("preflight")
    run = PreflightRun.objects.first()
    assert run.overall_status == "error"
    assert run.errors == 1
    assert run.warnings == 1
    assert run.passed == 1


@pytest.mark.django_db
def test_preflight_overall_status_warn(monkeypatch):
    def fake_run_all(base_dir):
        return [
            CheckResult(level="ok", message="fine"),
            CheckResult(level="warn", message="hmm"),
        ]

    monkeypatch.setattr("apps.checkers.management.commands.preflight.run_all", fake_run_all)
    call_command("preflight")
    run = PreflightRun.objects.first()
    assert run.overall_status == "warn"


@pytest.mark.django_db
def test_preflight_overall_status_ok(monkeypatch):
    def fake_run_all(base_dir):
        return [CheckResult(level="ok", message="fine")]

    monkeypatch.setattr("apps.checkers.management.commands.preflight.run_all", fake_run_all)
    call_command("preflight")
    run = PreflightRun.objects.first()
    assert run.overall_status == "ok"
