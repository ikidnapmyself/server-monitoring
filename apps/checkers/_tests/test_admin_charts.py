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


def test_defaults_are_unchanged_by_the_new_arguments():
    points = [(0, 10.0), (1, 20.0)]
    assert render_sparkline(points) == render_sparkline(points, title="")


def test_a_title_is_rendered_and_escaped():
    svg = render_sparkline([(0, 10.0)], title="Disk <b>usage</b>")
    assert "&lt;b&gt;" in svg
    assert "<b>" not in svg


def test_axis_labels_show_the_series_range():
    svg = render_sparkline([(0, 10.0), (1, 90.0)], show_axis=True)
    assert "90" in svg
    assert "10" in svg


def test_axis_labels_are_off_by_default():
    svg = render_sparkline([(0, 10.0), (1, 90.0)])
    # Assert on the label element, not the bare digits: coordinate math could
    # emit "90" inside a path attribute by coincidence at some width/height.
    assert "spark-axis" not in svg
    assert "<text" not in svg


def test_title_and_axis_reserve_space_instead_of_overlapping_the_plot():
    svg = render_sparkline([(0, 10.0), (1, 90.0)], title="CPU", show_axis=True)
    # viewBox grows by the title band and the axis gutter; the plot is shifted
    # into the remaining area rather than drawn under the labels.
    assert 'viewBox="0 0 146 36"' in svg
    assert "translate(26, 12)" in svg
