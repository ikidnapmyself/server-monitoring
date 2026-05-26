"""Tests for apps.observability.formatter."""

import json
import logging

from apps.observability import context
from apps.observability.formatter import JsonLineFormatter, PrettyConsoleFormatter


def make_record(name="apps.alerts.services", level=logging.INFO, msg="hello", **extra):
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="x.py",
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_minimal_record_round_trips_as_json():
    fmt = JsonLineFormatter()
    out = fmt.format(make_record())
    obj = json.loads(out)
    assert obj["msg"] == "hello"
    assert obj["level"] == "INFO"
    assert obj["logger"] == "apps.alerts.services"
    assert obj["v"] == 1
    assert obj["ts"].endswith("Z")
    assert "instance_id" in obj


def test_contextvars_appear_in_record(monkeypatch):
    fmt = JsonLineFormatter()
    token = context.bind(trace_id="t1", run_id="r1", incident_id=42)
    try:
        out = fmt.format(make_record())
    finally:
        context.restore(token)
    obj = json.loads(out)
    assert obj["trace_id"] == "t1"
    assert obj["run_id"] == "r1"
    assert obj["incident_id"] == 42


def test_unset_contextvars_omitted_from_output():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert "trace_id" not in obj
    assert "run_id" not in obj
    assert "incident_id" not in obj


def test_extra_kwargs_land_under_extra_key():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(severity="warning", count=3)))
    assert obj["extra"] == {"severity": "warning", "count": 3}


def test_reserved_log_record_keys_excluded_from_extra():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert "extra" not in obj or "name" not in obj.get("extra", {})


def test_category_from_extra_overrides_default():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(category="custom-cat")))
    assert obj["category"] == "custom-cat"


def test_category_resolved_from_logger_prefix():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(name="apps.notify.drivers.slack")))
    assert obj["category"] == "notify"


def test_exception_serialized_into_three_fields():
    import sys

    fmt = JsonLineFormatter()
    exc_info = None
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="x",
        level=logging.ERROR,
        pathname="x.py",
        lineno=1,
        msg="failed",
        args=None,
        exc_info=exc_info,
    )
    obj = json.loads(fmt.format(record))
    assert obj["exc_type"] == "ValueError"
    assert obj["exc_msg"] == "boom"
    assert "Traceback" in obj["exc_stack"]


def test_unserialisable_object_stringifies_not_raises():
    fmt = JsonLineFormatter()

    class Weird:
        def __repr__(self):
            return "<weird>"

    out = fmt.format(make_record(thing=Weird()))
    obj = json.loads(out)
    assert obj["extra"]["thing"] == "<weird>"


def test_instance_id_falls_back_to_hostname(monkeypatch):
    monkeypatch.setattr("django.conf.settings.INSTANCE_ID", "", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "test-host")
    import apps.observability.formatter as fmt_mod

    fmt_mod._INSTANCE_ID = None  # reset memo
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["instance_id"] == "test-host"


def test_instance_id_uses_settings_when_set(monkeypatch):
    monkeypatch.setattr("django.conf.settings.INSTANCE_ID", "prod-instance-7", raising=False)
    # Reset the memo cache if Fix 2 is applied; otherwise the assignment alone is enough
    import apps.observability.formatter as fmt_mod

    fmt_mod._INSTANCE_ID = None  # safe even before Fix 2 — attr just doesn't exist yet
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["instance_id"] == "prod-instance-7"


def test_caller_supplied_context_keys_in_extra_are_dropped():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(trace_id="caller-supplied", count=5)))
    # trace_id from extra is dropped (no top-level emission because context unset, no extra echo)
    assert "trace_id" not in obj
    assert obj.get("extra", {}).get("trace_id") is None
    # Genuine extras still appear
    assert obj["extra"]["count"] == 5


def test_heartbeat_promoted_keys_appear_at_top_level():
    fmt = JsonLineFormatter()
    record = make_record(
        name="apps.observability.heartbeat",
        msg="heartbeat",
        _hb_name="check_health.hourly",
        _hb_status="ok",
        _hb_duration_ms=12.3,
        _hb_metrics={"checks_run": 5},
    )
    obj = json.loads(fmt.format(record))
    assert obj["name"] == "check_health.hourly"
    assert obj["status"] == "ok"
    assert obj["duration_ms"] == 12.3
    assert obj["metrics"] == {"checks_run": 5}
    # And the underscore-prefixed keys must NOT appear in extra (they're reserved)
    assert "_hb_name" not in obj.get("extra", {})


def test_pretty_formatter_renders_human_readable_line():
    fmt = PrettyConsoleFormatter()
    out = fmt.format(make_record())
    # Sample expected shape: "14:23:01  INFO  apps.alerts.services  hello"
    assert "INFO" in out
    assert "apps.alerts.services" in out
    assert "hello" in out


def test_pretty_formatter_includes_trace_run_when_present():
    fmt = PrettyConsoleFormatter()
    token = context.bind(trace_id="1234abcd-deadbeef", run_id="abcd1234-feedface")
    try:
        out = fmt.format(make_record())
    finally:
        context.restore(token)
    # First 8 chars of trace/run id
    assert "1234abcd" in out
    assert "abcd1234" in out


def test_pretty_formatter_omits_trace_when_unset():
    fmt = PrettyConsoleFormatter()
    out = fmt.format(make_record())
    assert "trace=" not in out
    assert "run=" not in out


def test_record_has_record_id_uuid():
    import uuid

    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert "record_id" in obj
    parsed = uuid.UUID(obj["record_id"])
    assert parsed.version == 4


def test_record_id_unique_per_emission():
    fmt = JsonLineFormatter()
    a = json.loads(fmt.format(make_record()))
    b = json.loads(fmt.format(make_record()))
    assert a["record_id"] != b["record_id"]


def test_record_has_empty_path_at_emit():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["path"] == []


def test_record_id_and_path_cannot_be_spoofed_via_extra():
    # A caller passing extra={"record_id": "forged", "path": ["forged"]} must
    # not be able to defeat cluster dedup or loop-break. Both fields are
    # stamped by the formatter itself and are in _RESERVED_RECORD_KEYS so
    # the forged values are dropped before reaching either obj or obj["extra"].
    import uuid

    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(record_id="forged-id", path=["forged-host"])))
    assert obj["record_id"] != "forged-id"
    uuid.UUID(obj["record_id"])  # is a real uuid, not a forged string
    assert obj["path"] == []
    assert "record_id" not in obj.get("extra", {})
    assert "path" not in obj.get("extra", {})


def test_pretty_formatter_renders_exception_block():
    import sys

    exc_info = None
    try:
        raise RuntimeError("nope")
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="x",
        level=logging.ERROR,
        pathname="x.py",
        lineno=1,
        msg="oops",
        args=None,
        exc_info=exc_info,
    )
    fmt = PrettyConsoleFormatter()
    out = fmt.format(record)
    assert "RuntimeError: nope" in out
