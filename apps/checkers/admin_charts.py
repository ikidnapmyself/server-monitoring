"""Self-contained inline SVG charts for the Django admin.

No JavaScript, no external assets (fonts/CDN/images), no ``xmlns`` — the markup
is inlined directly into admin HTML5 pages, so it must not reference any external
host (it renders under the same self-contained constraints as an artifact). Colors
use ``currentColor`` so the chart follows the admin's light/dark text color.

All markup is assembled with ``format_html``/``format_html_join`` (never
``mark_safe``): the templates are static literals and every interpolated value is
a number or an escaped string, so there is no XSS surface and the security linters
stay clean.
"""

from collections.abc import Iterable, Sequence

from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

Point = tuple[float, float]

# Space reserved above the plot for the title, and to its left for axis labels.
TITLE_BAND = 12
AXIS_GUTTER = 26


def render_sparkline(
    points: Sequence[Point],
    markers: Iterable[float] | None = None,
    width: int = 120,
    height: int = 24,
    pad: int = 2,
    title: str = "",
    show_axis: bool = False,
) -> SafeString:
    """Render a tiny inline SVG line chart from ``(x, y)`` points.

    ``markers`` is an optional set of x-values to highlight with a dot (e.g. the
    times an alert fired). ``title`` draws a caption above the plot, and
    ``show_axis`` labels the series maximum and minimum down the left edge. Both
    grow the viewBox and shift the plot, so neither ever overlaps the line.
    Returns a ``SafeString`` of self-contained ``<svg>`` markup. Handles empty
    and single-point series and flat (zero-span) series without raising.
    """
    marker_xs = set(markers or ())
    left = AXIS_GUTTER if show_axis else 0
    top = TITLE_BAND if title else 0

    title_svg: str | SafeString = ""
    if title:
        title_svg = format_html(
            '<text class="spark-title" x="{}" y="9" font-size="10" fill="currentColor">{}</text>',
            left,
            title,
        )

    axis_svg: str | SafeString = ""
    plot: str | SafeString = ""

    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = (xmax - xmin) or 1
        yspan = (ymax - ymin) or 1

        def sx(x: float) -> str:
            return f"{pad + (x - xmin) / xspan * (width - 2 * pad):.1f}"

        def sy(y: float) -> str:
            # SVG y grows downward; invert so larger values sit higher.
            return f"{height - pad - (y - ymin) / yspan * (height - 2 * pad):.1f}"

        if len(points) == 1:
            x, y = points[0]
            body = format_html('<circle cx="{}" cy="{}" r="2" fill="currentColor"/>', sx(x), sy(y))
        else:
            coords = " ".join(f"{sx(x)},{sy(y)}" for x, y in points)
            body = format_html(
                '<polyline points="{}" fill="none" stroke="currentColor" stroke-width="1"/>',
                coords,
            )

        markers_svg = format_html_join(
            "",
            '<circle cx="{}" cy="{}" r="2" fill="#d33"/>',
            ((sx(x), sy(y)) for x, y in points if x in marker_xs),
        )
        plot = body + markers_svg

        if show_axis:
            axis_svg = format_html(
                '<text class="spark-axis" x="{}" y="{}" font-size="9"'
                ' text-anchor="end" fill="currentColor">{}</text>'
                '<text class="spark-axis" x="{}" y="{}" font-size="9"'
                ' text-anchor="end" fill="currentColor">{}</text>',
                left - 4,
                top + pad + 7,
                f"{ymax:g}",
                left - 4,
                top + height - pad,
                f"{ymin:g}",
            )

    if left or top:
        plot = format_html('<g transform="translate({}, {})">{}</g>', left, top, plot)

    return format_html(
        '<svg viewBox="0 0 {} {}" width="{}" height="{}" role="img">{}{}{}</svg>',
        width + left,
        height + top,
        width + left,
        height + top,
        title_svg,
        axis_svg,
        plot,
    )
