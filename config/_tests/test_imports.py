"""``config`` sits under the apps, so nothing in it reaches into the console."""

import re
from pathlib import Path

import pytest
from django.conf import settings

CONSOLE_IMPORT = re.compile(
    r"^\s*(?:(?:from|import)\s+apps\.console\b|from\s+apps\s+import\s+[^\n]*\bconsole\b)",
    re.M,
)


@pytest.mark.parametrize(
    "line",
    [
        "import apps.console",
        "from apps.console.intents import resolve_action",
        "    from apps import console",
        "from apps import alerts, console",
    ],
)
def test_the_pattern_catches_every_way_to_import_the_console(line):
    assert CONSOLE_IMPORT.search(line)


@pytest.mark.parametrize("line", ["from apps import consoles", "from apps.alerts import x"])
def test_the_pattern_leaves_other_imports_alone(line):
    assert not CONSOLE_IMPORT.search(line)


def test_nothing_in_config_imports_the_console():
    root = Path(settings.BASE_DIR) / "config"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "_tests" not in path.parts and CONSOLE_IMPORT.search(path.read_text())
    ]

    assert offenders == []
