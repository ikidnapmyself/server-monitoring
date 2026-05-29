"""Tests for `manage.py cluster_dest_doctor <name>`."""

import json
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _make_dest(name="central", url="https://central.example.com"):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    return ClusterDestination.objects.create(name=name, hub_url=url, api_key=key)


def _mock_http_response(status=200):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.mark.django_db
def test_all_checks_pass_https(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "central.example.com"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    out = capsys.readouterr().out
    assert "[✓] dns:" in out
    assert "93.184.216.34" in out
    assert "[✓] tcp:" in out
    assert "[✓] tls:" in out
    assert "central.example.com" in out
    assert "[✓] http:" in out
    assert "Summary: 4/4 checks passed" in out


@pytest.mark.django_db
def test_all_checks_pass_http_skips_tls(capsys):
    _make_dest(url="http://central.example.com")
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        call_command("cluster_dest_doctor", "central")
    out = capsys.readouterr().out
    assert "[✓] dns:" in out
    assert "[✓] tcp:" in out
    assert "tls" not in out
    assert "[✓] http:" in out
    assert "Summary: 3/3 checks passed" in out


@pytest.mark.django_db
def test_all_checks_pass_json_https(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "central.example.com"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["destination"] == "central"
    assert payload["summary"] == {"ok": 4, "fail": 0, "total": 4}
    names = [c["name"] for c in payload["checks"]]
    assert names == ["dns", "tcp", "tls", "http"]
    for c in payload["checks"]:
        assert c["ok"] is True
        assert isinstance(c["detail"], str)


@pytest.mark.django_db
def test_all_checks_pass_json_http_omits_tls(capsys):
    _make_dest(url="http://central.example.com")
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        call_command("cluster_dest_doctor", "central", "--json")
    payload = json.loads(capsys.readouterr().out)
    names = [c["name"] for c in payload["checks"]]
    assert names == ["dns", "tcp", "http"]
    assert payload["summary"] == {"ok": 3, "fail": 0, "total": 3}


@pytest.mark.django_db
def test_dns_failure_exits_1(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", side_effect=socket.gaierror("nodename nor servname")),
        pytest.raises(SystemExit) as exc,
    ):
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] dns:" in out
    assert "nodename nor servname" in out
    assert "Summary: 0/1 checks passed" in out


@pytest.mark.django_db
def test_tcp_failure_exits_1(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection", side_effect=OSError("connection refused")),
        pytest.raises(SystemExit) as exc,
    ):
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✓] dns:" in out
    assert "[✗] tcp:" in out
    assert "connection refused" in out
    assert "Summary: 1/2 checks passed" in out


@pytest.mark.django_db
def test_tls_failure_exits_1(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket", side_effect=ssl.SSLError("handshake failure")),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✓] dns:" in out
    assert "[✓] tcp:" in out
    assert "[✗] tls:" in out
    assert "handshake failure" in out
    assert "Summary: 2/3 checks passed" in out


@pytest.mark.django_db
def test_http_401_no_secret_reports_auth_required(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 401, "Unauthorized", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    # No secret was sent, so a 401 means auth is required, not that a
    # supplied credential was rejected.
    assert "auth required" in out
    assert "--secret" in out
    assert "Summary: 3/4 checks passed" in out


@pytest.mark.django_db
def test_http_401_with_secret_reports_auth_rejected(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 401, "Unauthorized", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--secret", "wrong-key")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    # A secret was supplied and rejected -> credential problem.
    assert "auth rejected" in out


@pytest.mark.django_db
def test_http_403_with_secret_reports_auth_rejected(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 403, "Forbidden", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--secret", "wrong-key")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    assert "auth rejected" in out


@pytest.mark.django_db
def test_http_404_reports_generic_not_found(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 404, "Not Found", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    # Generic, non-misleading: a 404 can mean a wrong hub_url, not only a
    # not-yet-deployed endpoint. The probe path is included so operators can
    # spot a wrong path prefix.
    assert "endpoint not found" in out
    assert "/cluster/logs/health/" in out
    assert "PR 2" not in out


@pytest.mark.django_db
def test_http_500_hub_error(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 503, "Service Unavailable", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    assert "hub error: 503" in out


@pytest.mark.django_db
def test_http_other_4xx_shows_status(capsys):
    _make_dest()
    from urllib.error import HTTPError

    err = HTTPError("u", 429, "Too Many Requests", {}, None)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=err,
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    assert "HTTP 429" in out


@pytest.mark.django_db
def test_http_network_error(capsys):
    _make_dest()
    from urllib.error import URLError

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=URLError("timed out"),
        ),
        pytest.raises(SystemExit) as exc,
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    assert "timed out" in out


@pytest.mark.django_db
def test_http_200_passes(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(204),
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    out = capsys.readouterr().out
    assert "[✓] http:" in out
    assert "Summary: 4/4 checks passed" in out


@pytest.mark.django_db
def test_http_request_head_method_no_secret_omits_auth(capsys):
    _make_dest()
    captured = {}

    def fake_urlopen(request, *, allowed_hosts=(), timeout=30):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.get_header("Authorization")
        return _mock_http_response(200)

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=fake_urlopen,
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert captured["url"] == "https://central.example.com/cluster/logs/health/"
    assert captured["method"] == "HEAD"
    # No secret supplied -> no Authorization header (probe checks reachability only).
    assert captured["auth"] is None


@pytest.mark.django_db
def test_http_request_secret_flag_sends_bearer(capsys):
    _make_dest()
    captured = {}

    def fake_urlopen(request, *, allowed_hosts=(), timeout=30):
        captured["auth"] = request.get_header("Authorization")
        return _mock_http_response(200)

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=fake_urlopen,
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--secret", "raw-secret-123")
    assert captured["auth"] == "Bearer raw-secret-123"


@pytest.mark.django_db
def test_http_request_secret_env_var_sends_bearer(capsys, monkeypatch):
    _make_dest()
    monkeypatch.setenv("CLUSTER_HUB_SECRET", "env-secret-456")
    captured = {}

    def fake_urlopen(request, *, allowed_hosts=(), timeout=30):
        captured["auth"] = request.get_header("Authorization")
        return _mock_http_response(200)

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=fake_urlopen,
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert captured["auth"] == "Bearer env-secret-456"


@pytest.mark.django_db
def test_secret_flag_overrides_env_var(capsys, monkeypatch):
    _make_dest()
    monkeypatch.setenv("CLUSTER_HUB_SECRET", "env-secret")
    captured = {}

    def fake_urlopen(request, *, allowed_hosts=(), timeout=30):
        captured["auth"] = request.get_header("Authorization")
        return _mock_http_response(200)

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            side_effect=fake_urlopen,
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--secret", "flag-secret")
    assert captured["auth"] == "Bearer flag-secret"


@pytest.mark.django_db
def test_unknown_destination_raises():
    with pytest.raises(CommandError, match="No destination named 'ghost'"):
        call_command("cluster_dest_doctor", "ghost")


@pytest.mark.django_db
def test_dns_failure_json(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", side_effect=socket.gaierror("boom")),
        pytest.raises(SystemExit) as exc,
    ):
        call_command("cluster_dest_doctor", "central", "--json")
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["destination"] == "central"
    assert payload["summary"] == {"ok": 0, "fail": 1, "total": 1}
    assert payload["checks"][0] == {"name": "dns", "ok": False, "detail": "boom"}


@pytest.mark.django_db
def test_tls_check_uses_timeout_option(capsys):
    _make_dest()
    captured = {}

    def fake_create_connection(addr, timeout=None):
        captured["timeout"] = timeout
        captured["addr"] = addr
        return MagicMock()

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection", side_effect=fake_create_connection),
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central", "--timeout", "9")
    assert captured["timeout"] == 9
    assert captured["addr"] == ("central.example.com", 443)


@pytest.mark.django_db
def test_default_port_http_is_80(capsys):
    _make_dest(url="http://plain.example.com")
    captured = {}

    def fake_create_connection(addr, timeout=None):
        captured["addr"] = addr
        return MagicMock()

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection", side_effect=fake_create_connection),
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        call_command("cluster_dest_doctor", "central")
    assert captured["addr"] == ("plain.example.com", 80)


@pytest.mark.django_db
def test_explicit_port_used(capsys):
    _make_dest(url="https://central.example.com:8443")
    captured = {}

    def fake_create_connection(addr, timeout=None):
        captured["addr"] = addr
        return MagicMock()

    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection", side_effect=fake_create_connection),
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    assert captured["addr"] == ("central.example.com", 8443)


@pytest.mark.django_db
def test_tls_peer_cert_missing_cn(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("organizationName", "ACME"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    out = capsys.readouterr().out
    assert "[✓] tls:" in out
    assert "(no CN)" in out


@pytest.mark.django_db
def test_tls_socket_closed_on_success(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = {"subject": ((("commonName", "h"),),)}
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    # The wrapped TLS socket must be closed so the probe does not leak an FD.
    tls.close.assert_called_once()


@pytest.mark.django_db
def test_tls_socket_closed_when_handshake_fails(capsys):
    _make_dest()
    tcp_sock = MagicMock()  # used by the TCP probe (step 2)
    tls_sock = MagicMock()  # used by the TLS probe (step 3)
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection", side_effect=[tcp_sock, tls_sock]),
        patch.object(ssl.SSLContext, "wrap_socket", side_effect=ssl.SSLError("boom")),
        pytest.raises(SystemExit),
    ):
        call_command("cluster_dest_doctor", "central")
    # wrap_socket raised before taking ownership, so the raw TCP socket from
    # the TLS probe must be closed directly to avoid leaking an FD.
    tls_sock.close.assert_called_once()


@pytest.mark.django_db
def test_tls_connect_failure_closes_nothing(capsys):
    _make_dest()
    tcp_sock = MagicMock()  # TCP probe (step 2) succeeds
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        # TCP probe connects; the TLS probe's own connect then fails.
        patch("socket.create_connection", side_effect=[tcp_sock, OSError("no route")]),
        pytest.raises(SystemExit) as exc,
    ):
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] tls:" in out
    assert "no route" in out
    # The TLS connect never returned a socket, so there is nothing to close
    # and no FD is leaked (the TCP probe closed its own socket already).
    tcp_sock.close.assert_called_once()


@pytest.mark.django_db
def test_tls_peer_cert_none(capsys):
    _make_dest()
    with (
        patch("socket.gethostbyname", return_value="93.184.216.34"),
        patch("socket.create_connection") as mock_conn,
        patch.object(ssl.SSLContext, "wrap_socket") as mock_wrap,
        patch(
            "apps.observability.management.commands.cluster_dest_doctor.safe_urlopen",
            return_value=_mock_http_response(200),
        ),
    ):
        mock_conn.return_value = MagicMock()
        tls = MagicMock()
        tls.getpeercert.return_value = None
        mock_wrap.return_value = tls
        call_command("cluster_dest_doctor", "central")
    out = capsys.readouterr().out
    assert "(no cert)" in out
