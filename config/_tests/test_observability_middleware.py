"""Tests for ObservabilityMiddleware (trace_id/source binding)."""

import uuid

import pytest
from django.test import RequestFactory

from apps.observability import context
from config.middleware.observability import ObservabilityMiddleware


def _make_response_view(captured: dict):
    def view(request):
        captured["trace_id_during_request"] = context.snapshot()["trace_id"]
        captured["source_during_request"] = context.snapshot()["source"]
        from django.http import HttpResponse

        return HttpResponse("ok")

    return view


def test_middleware_generates_trace_id_when_header_absent():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    req = RequestFactory().get("/")
    mw(req)
    assert captured["trace_id_during_request"]
    uuid.UUID(captured["trace_id_during_request"])  # parses as uuid


def test_middleware_uses_x_trace_id_header_when_present():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    req = RequestFactory().get("/", HTTP_X_TRACE_ID="caller-supplied-123")
    mw(req)
    assert captured["trace_id_during_request"] == "caller-supplied-123"


def test_middleware_sets_source_http():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    mw(RequestFactory().get("/"))
    assert captured["source_during_request"] == "http"


def test_middleware_clears_context_after_response():
    mw = ObservabilityMiddleware(_make_response_view({}))
    mw(RequestFactory().get("/"))
    assert context.snapshot()["trace_id"] is None
    assert context.snapshot()["source"] is None


def test_middleware_clears_context_on_exception():
    def bad_view(request):
        raise ValueError("explode")

    mw = ObservabilityMiddleware(bad_view)
    with pytest.raises(ValueError):
        mw(RequestFactory().get("/"))
    assert context.snapshot()["trace_id"] is None
