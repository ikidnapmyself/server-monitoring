"""Tests for the inline SVG sparkline helper."""

from django.utils.safestring import SafeString

from apps.checkers.admin_charts import render_sparkline


def test_sparkline_returns_inline_svg_safestring():
    out = render_sparkline([(1, 10.0), (2, 20.0), (3, 15.0)])
    assert isinstance(out, SafeString)
    assert out.startswith("<svg")
    # Self-contained: no external references of any kind.
    assert "http://" not in out and "https://" not in out
    assert "<script" not in out
    assert "polyline" in out


def test_sparkline_empty_series_is_safe():
    out = render_sparkline([])
    assert isinstance(out, SafeString)
    assert out.startswith("<svg")
    assert "polyline" not in out


def test_sparkline_single_point_does_not_crash():
    out = render_sparkline([(1, 42.0)])
    assert out.startswith("<svg")
    # A single point can't form a line, so it renders as a dot.
    assert "circle" in out
    assert "polyline" not in out


def test_sparkline_renders_markers():
    out = render_sparkline([(1, 10.0), (2, 90.0)], markers=[2])
    assert "polyline" in out
    assert "circle" in out  # the alert marker


def test_sparkline_flat_series_does_not_divide_by_zero():
    # All y-values equal -> zero span; must not raise.
    out = render_sparkline([(1, 5.0), (2, 5.0), (3, 5.0)])
    assert out.startswith("<svg")
    assert "polyline" in out
