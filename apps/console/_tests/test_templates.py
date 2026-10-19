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


def test_every_icon_a_template_names_is_in_the_sprite():
    sprite = (
        Path(settings.BASE_DIR) / "static" / "console" / "vendor" / "octicons.svg"
    ).read_text()
    named = {
        name
        for template in TEMPLATES
        for name in re.findall(r'_octicon\.html" with name="([a-z-]+)"', template.read_text())
    }

    assert named
    assert {name for name in named if f'id="octicon-{name}"' not in sprite} == set()


@pytest.mark.django_db
def test_the_nav_has_a_system_item(client, superuser):
    client.force_login(superuser)
    body = client.get("/console/").content.decode()
    assert 'href="/console/routes/"' in body
    assert "#octicon-gear" in body
