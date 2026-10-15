"""Console templates carry no inline script, style or handlers.

The CSP would block them in a browser. This fails in CI first.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

TEMPLATES = sorted((Path(settings.BASE_DIR) / "templates" / "console").rglob("*.html"))

FORBIDDEN = {
    "style element": re.compile(r"<style\b", re.I),
    "style attribute": re.compile(r"\sstyle\s*=", re.I),
    "inline script": re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I),
    "event handler attribute": re.compile(r"\son[a-z]+\s*=", re.I),
    "hx-on attribute": re.compile(r"\shx-on", re.I),
    "javascript url": re.compile(r"javascript:", re.I),
    "external url": re.compile(r"(?:src|href)\s*=\s*[\"'](?:https?:)?//", re.I),
}


def test_there_are_templates_to_scan():
    assert TEMPLATES


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_template_is_clean(template, label):
    assert not FORBIDDEN[label].search(template.read_text()), f"{label} in {template}"
