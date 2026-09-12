"""The ops skin's two dark blocks must stay in sync.

The auto state stamps no attribute on the root, so it cannot share a selector list
with [data-theme="dark"] and the token set has to be written twice. Nothing but a
test stops the next person from updating one block and forgetting the other.
"""

import re
from pathlib import Path

from django.conf import settings

OPS_CSS = Path(settings.BASE_DIR) / "static" / "admin" / "css" / "ops.css"


def _declared_properties(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


def _block_after(marker: str) -> str:
    text = OPS_CSS.read_text()
    start = text.index(marker) + len(marker)
    end = text.index("}", start)
    return text[start:end]


def test_auto_dark_declares_the_same_tokens_as_explicit_dark():
    explicit = _declared_properties(_block_after('[data-theme="dark"] {'))
    auto = _declared_properties(_block_after(':root:not([data-theme="light"]) {'))

    assert explicit, 'no tokens found in the [data-theme="dark"] block'
    assert explicit == auto
