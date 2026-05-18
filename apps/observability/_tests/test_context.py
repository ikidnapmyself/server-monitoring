"""Tests for apps.observability.context."""

import asyncio
import threading

import pytest

from apps.observability import context


def test_default_values_are_none():
    snap = context.snapshot()
    assert snap == {
        "trace_id": None,
        "run_id": None,
        "incident_id": None,
        "stage": None,
        "source": None,
    }


def test_bind_sets_fields():
    token = context.bind(trace_id="abc", source="http")
    try:
        snap = context.snapshot()
        assert snap["trace_id"] == "abc"
        assert snap["source"] == "http"
        assert snap["run_id"] is None
    finally:
        context.restore(token)


def test_restore_returns_to_previous_state():
    token = context.bind(trace_id="abc")
    context.restore(token)
    assert context.snapshot()["trace_id"] is None


def test_nested_bind_restores_correctly():
    outer = context.bind(trace_id="outer")
    inner = context.bind(trace_id="inner", run_id="r1")
    assert context.snapshot()["trace_id"] == "inner"
    assert context.snapshot()["run_id"] == "r1"
    context.restore(inner)
    assert context.snapshot()["trace_id"] == "outer"
    assert context.snapshot()["run_id"] is None
    context.restore(outer)
    assert context.snapshot()["trace_id"] is None


def test_threads_have_isolated_context():
    results: list[str | None] = []

    def worker():
        results.append(context.snapshot()["trace_id"])

    token = context.bind(trace_id="main-thread")
    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        context.restore(token)

    assert results == [None]


def test_asyncio_tasks_have_isolated_context():
    async def child():
        return context.snapshot()["trace_id"]

    async def parent():
        token = context.bind(trace_id="parent")
        try:
            # New task copies parent's context at creation time
            task = asyncio.create_task(child())
            return await task
        finally:
            context.restore(token)

    result = asyncio.run(parent())
    assert result == "parent"


def test_bind_unknown_field_raises_keyerror_and_rolls_back():
    # Dict insertion order: trace_id is bound first, then the unknown name fails
    with pytest.raises(KeyError, match="unknown context field"):
        context.bind(trace_id="leaked", bogus="x")
    assert context.snapshot()["trace_id"] is None
