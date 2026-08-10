"""Self-contained inline SVG charts for the Django admin.

No JavaScript, no external assets (fonts/CDN/images), no ``xmlns`` — the markup
is inlined directly into admin HTML5 pages, so it must not reference any external
host (it renders under the same self-contained constraints as an artifact). Colors
use ``currentColor`` so the chart follows the admin's light/dark text color.
"""

from collections.abc import Iterable, Sequence

from django.utils.safestring import SafeString, mark_safe

Point = tuple[float, float]


def render_sparkline(
    points: Sequence[Point],
    markers: Iterable[float] | None = None,
    width: int = 120,
    height: int = 24,
    pad: int = 2,
) -> SafeString:
    """Render a tiny inline SVG line chart from ``(x, y)`` points.

    ``markers`` is an optional set of x-values to highlight with a dot (e.g. the
    times an alert fired). Returns a ``SafeString`` of self-contained ``<svg>``
    markup. Handles empty and single-point series and flat (zero-span) series
    without raising.
    """
    marker_xs = set(markers or ())
    open_tag = f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">'

    if not points:
        return mark_safe(f"{open_tag}</svg>")

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xspan = (xmax - xmin) or 1
    yspan = (ymax - ymin) or 1

    def sx(x: float) -> float:
        return pad + (x - xmin) / xspan * (width - 2 * pad)

    def sy(y: float) -> float:
        # SVG y grows downward; invert so larger values sit higher.
        return height - pad - (y - ymin) / yspan * (height - 2 * pad)

    parts = [open_tag]
    if len(points) == 1:
        x, y = points[0]
        parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2" fill="currentColor"/>')
    else:
        coords = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in points)
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="currentColor" stroke-width="1"/>'
        )

    for x, y in points:
        if x in marker_xs:
            parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2" fill="#d33"/>')

    parts.append("</svg>")
    return mark_safe("".join(parts))
