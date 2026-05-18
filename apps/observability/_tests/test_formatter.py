"""Tests for apps.observability.formatter."""

import json
import logging

from apps.observability import context
from apps.observability.formatter import JsonLineFormatter


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
    fmt = JsonLineFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="x",
            level=logging.ERROR,
            pathname="x.py",
            lineno=1,
            msg="failed",
            args=None,
            exc_info=sys.exc_info(),
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
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["instance_id"] == "test-host"
