"""Django system checks for apps.observability."""

import os
from unittest.mock import patch


def test_w001_passes_when_logs_dir_is_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from apps.observability.checks import check_logs_dir_writable

    errs = check_logs_dir_writable(None)
    assert errs == []


def test_w001_fails_when_logs_dir_is_not_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    os.chmod(tmp_path, 0o555)
    try:
        from apps.observability.checks import check_logs_dir_writable

        errs = check_logs_dir_writable(None)
        assert any(e.id == "observability.W001" for e in errs)
    finally:
        os.chmod(tmp_path, 0o755)


def test_w001_short_circuits_when_logs_dir_is_falsy(settings):
    """Guard branch: if Path() evaluates falsy, return [] without touching disk."""
    from apps.observability import checks as obs_checks

    with patch.object(obs_checks, "Path") as mock_path:
        mock_path.return_value = False  # force `if not logs_dir:` to be True
        settings.LOGS_DIR = ""
        errs = obs_checks.check_logs_dir_writable(None)
    assert errs == []


def test_w001_creates_logs_dir_when_missing(tmp_path, settings):
    """Covers the `mkdir` branch when LOGS_DIR does not yet exist."""
    target = tmp_path / "nested" / "logs"
    assert not target.exists()
    settings.LOGS_DIR = target
    from apps.observability.checks import check_logs_dir_writable

    errs = check_logs_dir_writable(None)
    assert errs == []
    assert target.exists()


def test_w001_warns_when_oserror_raised(tmp_path, settings):
    """Covers the OSError except branch."""
    settings.LOGS_DIR = tmp_path
    from apps.observability import checks as obs_checks

    with patch.object(obs_checks.os, "access", side_effect=OSError("boom")):
        errs = obs_checks.check_logs_dir_writable(None)
    assert any(e.id == "observability.W001" for e in errs)
