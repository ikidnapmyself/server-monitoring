import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from config.security.url_validation import URLNotAllowedError


class TestSafeUrlopen:
    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_delegates_to_urlopen_for_valid_url(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        mock_validate.return_value = "https://example.com"
        mock_urlopen.return_value = MagicMock()

        result = safe_urlopen("https://example.com")

        mock_validate.assert_called_once_with("https://example.com", allowed_hosts=())
        mock_urlopen.assert_called_once_with("https://example.com", timeout=30)
        assert result is mock_urlopen.return_value

    @patch("config.security.http.urllib.request.urlopen")
    @patch(
        "config.security.http.validate_safe_url",
        side_effect=URLNotAllowedError("blocked"),
    )
    def test_raises_for_private_ip(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        with pytest.raises(URLNotAllowedError, match="blocked"):
            safe_urlopen("https://internal.corp")

    @patch("config.security.http.urllib.request.urlopen")
    @patch(
        "config.security.http.validate_safe_url",
        side_effect=URLNotAllowedError("blocked"),
    )
    def test_urlopen_not_called_on_validation_failure(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        with pytest.raises(URLNotAllowedError):
            safe_urlopen("https://internal.corp")

        mock_urlopen.assert_not_called()

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_passes_timeout_parameter(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        safe_urlopen("https://example.com", timeout=60)

        mock_urlopen.assert_called_once_with("https://example.com", timeout=60)

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_extracts_url_from_string(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        safe_urlopen("https://example.com/path?q=1")

        mock_validate.assert_called_once_with("https://example.com/path?q=1", allowed_hosts=())

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_extracts_url_from_request_object(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        req = urllib.request.Request("https://example.com/api")
        safe_urlopen(req)

        mock_validate.assert_called_once_with("https://example.com/api", allowed_hosts=())
        mock_urlopen.assert_called_once_with(req, timeout=30)

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_passes_allowed_hosts(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        hosts = ("internal.corp",)
        safe_urlopen("https://internal.corp/api", allowed_hosts=hosts)

        mock_validate.assert_called_once_with("https://internal.corp/api", allowed_hosts=hosts)

    @patch("config.security.http.urllib.request.urlopen")
    @patch("config.security.http.validate_safe_url")
    def test_works_as_context_manager(self, mock_validate, mock_urlopen):
        from config.security.http import safe_urlopen

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        with safe_urlopen("https://example.com") as resp:
            assert resp is mock_response
