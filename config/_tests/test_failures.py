import pytest

from config.failures import FAILURE_KINDS, classify_failure


class StatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class ResponseError(Exception):
    def __init__(self, status_code):
        self.response = Response(status_code)


class ReadTimeout(Exception):
    pass


class ConnectionRefused(Exception):
    pass


@pytest.mark.parametrize(
    "exc, kind",
    [
        (StatusError(401), "auth"),
        (StatusError(403), "auth"),
        (ResponseError(429), "rate_limit"),
        (StatusError(503), "provider_down"),
        (TimeoutError(), "timeout"),
        (ReadTimeout(), "timeout"),
        (ConnectionError(), "provider_down"),
        (ConnectionRefused(), "provider_down"),
        (StatusError(400), "rejected"),
        (ValueError("boom"), "unknown"),
        (StatusError("401"), "unknown"),
        (StatusError(True), "unknown"),
        (StatusError(302), "unknown"),
    ],
)
def test_classify_failure(exc, kind):
    assert classify_failure(exc) == kind


def test_every_kind_is_listed():
    assert set(FAILURE_KINDS) == {
        "auth",
        "rate_limit",
        "timeout",
        "provider_down",
        "rejected",
        "unknown",
    }
