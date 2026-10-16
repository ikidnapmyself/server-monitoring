"""Why a call to an outside service failed, as one of a few kinds a sysadmin can act on.

SDKs disagree on exception types, so this reads the HTTP status where one is exposed and
falls back to the exception's class name.
"""

FAILURE_KINDS = ("auth", "rate_limit", "timeout", "provider_down", "rejected", "unknown")


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        code = getattr(holder, "status_code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


def classify_failure(exc: BaseException) -> str:
    status = _status(exc)
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status is not None and status >= 500:
        return "provider_down"
    if status is not None and status >= 400:
        return "rejected"
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout"
    if isinstance(exc, ConnectionError) or "Connection" in type(exc).__name__:
        return "provider_down"
    return "unknown"
