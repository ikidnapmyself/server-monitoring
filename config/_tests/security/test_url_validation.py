from unittest.mock import patch

import pytest

from config.security.url_validation import URLNotAllowedError, validate_safe_url


def _mock_getaddrinfo(ip):
    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 443))]

    return _getaddrinfo


def _mock_getaddrinfo_v6(ip):
    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, port or 443, 0, 0))]

    return _getaddrinfo


def _mock_getaddrinfo_multi(*ips):
    def _getaddrinfo(host, port, *args, **kwargs):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 443)) for ip in ips]

    return _getaddrinfo


_PATCH_TARGET = "config.security.url_validation.socket.getaddrinfo"


class TestValidateSafeURLPublicIPs:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("93.184.216.34"))
    def test_public_ip_allowed(self, mock_dns):
        result = validate_safe_url("https://example.com/path")
        assert result == "https://example.com/path"

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("8.8.8.8"))
    def test_google_dns_allowed(self, mock_dns):
        result = validate_safe_url("https://dns.google")
        assert result == "https://dns.google"


class TestValidateSafeURLScheme:
    def test_ftp_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme must be http or https"):
            validate_safe_url("ftp://example.com/file")

    def test_file_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme must be http or https"):
            validate_safe_url("file:///etc/passwd")

    def test_no_scheme_rejected(self):
        with pytest.raises(URLNotAllowedError, match="scheme must be http or https"):
            validate_safe_url("example.com/path")

    def test_empty_url_rejected(self):
        with pytest.raises(URLNotAllowedError, match="empty URL"):
            validate_safe_url("")


class TestValidateSafeURLHostname:
    def test_missing_hostname_rejected(self):
        with pytest.raises(URLNotAllowedError, match="hostname is missing"):
            validate_safe_url("http://")


class TestValidateSafeURLPrivateIPs:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("10.0.0.1"))
    def test_10_x_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://internal.corp")

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("172.16.0.1"))
    def test_172_16_x_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://internal.corp")

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("192.168.1.1"))
    def test_192_168_x_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://internal.corp")


class TestValidateSafeURLLoopback:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("127.0.0.1"))
    def test_ipv4_loopback_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://localhost")

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo_v6("::1"))
    def test_ipv6_loopback_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://localhost")


class TestValidateSafeURLLinkLocal:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("169.254.169.254"))
    def test_metadata_endpoint_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://metadata.google.internal")

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo_v6("fe80::1"))
    def test_ipv6_link_local_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://link-local.test")


class TestValidateSafeURLMulticast:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("224.0.0.1"))
    def test_multicast_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://multicast.test")


class TestValidateSafeURLIPv6ULA:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo_v6("fd00::1"))
    def test_ula_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://ula.test")


class TestValidateSafeURLDNSFailure:
    @patch(_PATCH_TARGET, side_effect=OSError("Name resolution failed"))
    def test_dns_failure_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="could not resolve hostname"):
            validate_safe_url("https://nonexistent.invalid")


class TestValidateSafeURLDualHomed:
    @patch(
        _PATCH_TARGET,
        side_effect=_mock_getaddrinfo_multi("93.184.216.34", "10.0.0.1"),
    )
    def test_dual_homed_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url("https://dual-homed.example.com")


class TestValidateSafeURLAllowedHosts:
    def test_allowed_host_bypasses_check(self):
        result = validate_safe_url(
            "https://internal.corp/api",
            allowed_hosts=("internal.corp",),
        )
        assert result == "https://internal.corp/api"

    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("10.0.0.1"))
    def test_non_allowed_host_still_rejected(self, mock_dns):
        with pytest.raises(URLNotAllowedError, match="private/reserved"):
            validate_safe_url(
                "https://evil.corp",
                allowed_hosts=("internal.corp",),
            )


class TestValidateSafeURLWithPort:
    @patch(_PATCH_TARGET, side_effect=_mock_getaddrinfo("93.184.216.34"))
    def test_url_with_port(self, mock_dns):
        result = validate_safe_url("https://example.com:8443/path")
        assert result == "https://example.com:8443/path"
        mock_dns.assert_called_once_with("example.com", 8443)
