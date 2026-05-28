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
def test_http_401_auth_rejected(capsys):
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
    assert "auth rejected" in out
    assert "Summary: 3/4 checks passed" in out


@pytest.mark.django_db
def test_http_403_auth_rejected(capsys):
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
        call_command("cluster_dest_doctor", "central")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "[✗] http:" in out
    assert "auth rejected" in out


@pytest.mark.django_db
def test_http_404_pr2_placeholder(capsys):
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
    assert "endpoint not found (will exist in PR 2)" in out


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
def test_http_request_uses_auth_header_and_head_method(capsys):
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
    assert captured["auth"] == "ApiKey hub-key"


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
