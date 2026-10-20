"""Tests for ConfigField, one typed setting a form offers."""

import pytest

from config.config_fields import KINDS, ConfigField


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        ConfigField("x", "X", "number")


def test_headers_must_be_secret():
    with pytest.raises(ValueError, match="secret"):
        ConfigField("headers", "Headers", "headers")


def test_every_kind_builds():
    for kind in KINDS:
        field = ConfigField("x", "X", kind, secret=kind == "headers")
        assert field.kind == kind


def test_float_is_a_kind():
    assert "float" in KINDS
