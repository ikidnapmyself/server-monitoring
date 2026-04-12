from unittest.mock import MagicMock, patch
from urllib.request import Request  # noqa: TID251 — Request is a data object, not urlopen

import pytest

from config.security.url_validation import URLNotAllowedError


class TestSafeUrlopen:
    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_delegates_to_opener_for_valid_url(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_validate.return_value = "https://example.com"
        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener

        result = safe_urlopen("https://example.com")

        mock_validate.assert_called_once_with("https://example.com", allowed_hosts=())
        mock_opener.open.assert_called_once_with("https://example.com", timeout=30)
        assert result is mock_opener.open.return_value

    @patch("config.security.http.urllib.request.build_opener")
    @patch(
        "config.security.http.validate_safe_url",
        side_effect=URLNotAllowedError("blocked"),
    )
    def test_raises_for_private_ip(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        with pytest.raises(URLNotAllowedError, match="blocked"):
            safe_urlopen("https://internal.corp")

    @patch("config.security.http.urllib.request.build_opener")
    @patch(
        "config.security.http.validate_safe_url",
        side_effect=URLNotAllowedError("blocked"),
    )
    def test_opener_not_called_on_validation_failure(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener

        with pytest.raises(URLNotAllowedError):
            safe_urlopen("https://internal.corp")

        mock_opener.open.assert_not_called()

    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_passes_timeout_parameter(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener

        safe_urlopen("https://example.com", timeout=60)

        mock_opener.open.assert_called_once_with("https://example.com", timeout=60)

    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_extracts_url_from_string(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_build_opener.return_value = MagicMock()
        safe_urlopen("https://example.com/path?q=1")

        mock_validate.assert_called_once_with("https://example.com/path?q=1", allowed_hosts=())

    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_extracts_url_from_request_object(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener
        req = Request("https://example.com/api")
        safe_urlopen(req)

        mock_validate.assert_called_once_with("https://example.com/api", allowed_hosts=())
        mock_opener.open.assert_called_once_with(req, timeout=30)

    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_passes_allowed_hosts(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_build_opener.return_value = MagicMock()
        hosts = ("internal.corp",)
        safe_urlopen("https://internal.corp/api", allowed_hosts=hosts)

        mock_validate.assert_called_once_with("https://internal.corp/api", allowed_hosts=hosts)

    @patch("config.security.http.urllib.request.build_opener")
    @patch("config.security.http.validate_safe_url")
    def test_works_as_context_manager(self, mock_validate, mock_build_opener):
        from config.security.http import safe_urlopen

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        with safe_urlopen("https://example.com") as resp:
            assert resp is mock_response


class TestSSRFRedirectHandler:
    def test_allows_redirect_to_safe_url(self):
        from unittest.mock import MagicMock, patch

        from config.security.http import _SSRFRedirectHandler

        handler = _SSRFRedirectHandler(allowed_hosts=())
        req = MagicMock()
        req.get_method.return_value = "GET"
        req.host = "example.com"
        req.type = "https"
        req.unredirected_hdrs = {}
        req.headers = {}

        with patch("config.security.http.validate_safe_url") as mock_validate:
            mock_validate.return_value = "https://example.com/redirected"
            handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/redirected")
            mock_validate.assert_called_once_with(
                "https://example.com/redirected", allowed_hosts=()
            )

    def test_blocks_redirect_to_private_ip(self):
        from unittest.mock import MagicMock, patch

        from config.security.http import _SSRFRedirectHandler

        handler = _SSRFRedirectHandler(allowed_hosts=())
        req = MagicMock()

        with patch(
            "config.security.http.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            with pytest.raises(URLNotAllowedError, match="private"):
                handler.redirect_request(
                    req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
                )

    def test_redirect_handler_passes_allowed_hosts(self):
        from unittest.mock import MagicMock, patch

        from config.security.http import _SSRFRedirectHandler

        handler = _SSRFRedirectHandler(allowed_hosts=("internal.corp",))
        req = MagicMock()
        req.get_method.return_value = "GET"
        req.host = "internal.corp"
        req.type = "https"
        req.unredirected_hdrs = {}
        req.headers = {}

        with patch("config.security.http.validate_safe_url") as mock_validate:
            mock_validate.return_value = "https://internal.corp/new"
            handler.redirect_request(req, None, 301, "Moved", {}, "https://internal.corp/new")
            mock_validate.assert_called_once_with(
                "https://internal.corp/new", allowed_hosts=("internal.corp",)
            )

    def test_build_opener_uses_ssrf_redirect_handler(self):
        from config.security.http import _SSRFRedirectHandler, safe_urlopen

        with patch("config.security.http.urllib.request.build_opener") as mock_build_opener:
            with patch("config.security.http.validate_safe_url"):
                mock_build_opener.return_value = MagicMock()
                safe_urlopen("https://example.com")

            args, _ = mock_build_opener.call_args
            assert len(args) == 1
            assert isinstance(args[0], _SSRFRedirectHandler)


class TestRedactUrl:
    def test_redacts_path_and_query(self):
        from config.security.url_validation import _redact_url

        result = _redact_url("https://hooks.slack.com/services/TOKEN123/path?key=secret")
        assert result == "https://hooks.slack.com"
        assert "TOKEN123" not in result
        assert "secret" not in result

    def test_preserves_port(self):
        from config.security.url_validation import _redact_url

        result = _redact_url("https://example.com:8443/path")
        assert result == "https://example.com:8443"

    def test_handles_empty_url(self):
        from config.security.url_validation import _redact_url

        result = _redact_url("")
        assert result == "<invalid URL>"

    def test_http_scheme_preserved(self):
        from config.security.url_validation import _redact_url

        result = _redact_url("http://example.com/path?a=b")
        assert result == "http://example.com"

    def test_handles_parse_exception(self):
        from unittest.mock import patch

        from config.security.url_validation import _redact_url

        with patch(
            "config.security.url_validation.urllib.parse.urlparse",
            side_effect=Exception("parse error"),
        ):
            result = _redact_url("https://example.com")
            assert result == "<invalid URL>"
