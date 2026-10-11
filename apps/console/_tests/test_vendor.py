"""The vendor directory holds exactly what VENDOR.md says, byte for byte.

There is no package manager watching these files, so this is the only thing
that notices a swapped file or a second library added without review.
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings

VENDOR = Path(settings.BASE_DIR) / "static" / "console" / "vendor"
MANIFEST = VENDOR / "VENDOR.md"

ROW = re.compile(r"^\| `([^`]+)` \| ([^| ]+) \| (https://[^| ]+) \| `([0-9a-f]{64})` \|$", re.M)


def _manifest() -> dict[str, tuple[str, str]]:
    return {name: (version, sha) for name, version, _url, sha in ROW.findall(MANIFEST.read_text())}


def _rows() -> dict[str, str]:
    return {name: sha for name, (_version, sha) in _manifest().items()}


def test_manifest_lists_htmx():
    assert "htmx.min.js" in _rows()


def test_htmx_file_is_the_version_the_manifest_records():
    version, _sha = _manifest()["htmx.min.js"]

    assert f'version:"{version}"' in (VENDOR / "htmx.min.js").read_text()


def test_every_vendored_file_matches_its_recorded_hash():
    for name, recorded in _rows().items():
        actual = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert actual == recorded, f"{name} does not match VENDOR.md"


def test_vendor_directory_holds_only_what_the_manifest_lists():
    on_disk = {path.name for path in VENDOR.iterdir()}

    assert on_disk == set(_rows()) | {MANIFEST.name}
