from pathlib import Path

import pytest

from config.security.path_traversal import (
    ALLOWED_FILESYSTEM_ROOTS,
    PathNotAllowedError,
    resolve_safe_name,
    resolve_safe_path,
)


class TestResolveSafePath:
    def test_absolute_path_within_allowed_root(self):
        result = resolve_safe_path("/var/log", ALLOWED_FILESYSTEM_ROOTS)
        assert result == str(Path("/var/log").resolve())

    def test_root_path_allowed(self):
        result = resolve_safe_path("/", ALLOWED_FILESYSTEM_ROOTS)
        assert result == str(Path("/").resolve())

    def test_traversal_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("/../../../etc/shadow", ALLOWED_FILESYSTEM_ROOTS)

    def test_disallowed_path_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("/root/.ssh", ALLOWED_FILESYSTEM_ROOTS)

    def test_relative_path_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_path("relative/path", ("/usr",))

    def test_custom_allowed_roots(self):
        custom = (str(Path("/tmp").resolve()),)
        result = resolve_safe_path("/tmp/myfile.json", custom)
        assert result == str(Path("/tmp/myfile.json").resolve())

    def test_custom_root_rejects_outside(self):
        custom = (str(Path("/tmp").resolve()),)
        with pytest.raises(PathNotAllowedError):
            resolve_safe_path("/var/log", custom)

    def test_default_roots_are_resolved(self):
        for root in ALLOWED_FILESYSTEM_ROOTS:
            assert root == str(Path(root).resolve())


class TestResolveSafeName:
    def test_simple_name_allowed(self):
        assert resolve_safe_name("slack_text.j2") == "slack_text.j2"

    def test_name_with_hyphen_allowed(self):
        assert resolve_safe_name("my-template.j2") == "my-template.j2"

    def test_traversal_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("../../../etc/passwd")

    def test_slash_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("subdir/template.j2")

    def test_leading_dot_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name(".hidden")

    def test_backslash_in_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("sub\\template.j2")

    def test_empty_name_rejected(self):
        with pytest.raises(PathNotAllowedError, match="not allowed"):
            resolve_safe_name("")
