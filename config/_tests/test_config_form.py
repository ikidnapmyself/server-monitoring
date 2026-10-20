"""The typed settings form: what an operator edits, and what it writes back."""

import re

import pytest
from django import forms

from config.config_fields import ConfigField
from config.config_form import (
    ALIAS_HELP,
    HEADER_ERROR,
    SET_HELP,
    UNSET_HELP,
    TypedConfigForm,
    clear_name,
    field_name,
)

SPECS = (
    ConfigField("token", "Token", "text", secret=True, required=True, help="The token."),
    ConfigField("password", "Password", "text", secret=True),
    ConfigField("address", "Address", "url"),
    ConfigField("port", "Port", "int", default=30),
    ConfigField("ratio", "Ratio", "float"),
    ConfigField("enabled", "Enabled", "bool", default=False),
    ConfigField("method", "Method", "choice", default="POST", choices=(("POST", "Post"),)),
    ConfigField("region", "Region", "choice", choices=(("eu", "Europe"),)),
    ConfigField("recipients", "Recipients", "list"),
    ConfigField("body", "Body", "template"),
    ConfigField("headers", "Headers", "headers", secret=True),
    ConfigField("note", "Note", "text"),
)
ALIASES = {"old_password": "password"}
STORED = {
    "token": "token-secret-value",
    "address": "https://example.com/",
    "port": 10,
    "ratio": 0.5,
    "enabled": True,
    "method": "get",
    "recipients": ["a@example.com", "b@example.com"],
    "body": "a\r\nb",
    "headers": {"Authorization": "Bearer header-secret-value"},
    "extra": "unknown-value",
}


def form(data=None, stored=None):
    stored = STORED if stored is None else stored
    return TypedConfigForm(data, stored=stored, specs=SPECS, aliases=ALIASES)


def initial_data(shown):
    data = {}
    for bound in shown:
        value = bound.value()
        if isinstance(bound.field.widget, forms.CheckboxInput):
            if value:
                data[bound.html_name] = "on"
        elif value is not None:
            data[bound.html_name] = re.sub(r"\r?\n", "\r\n", str(value))
    return data


def resubmit(stored=None, **changes):
    data = initial_data(form(stored=stored))
    data.update(changes)
    return form(data, stored=stored)


def test_names_carry_the_prefix():
    assert field_name("port") == "cfg__port"
    assert clear_name("port") == "cfg__port__clear"


def test_an_untouched_save_writes_back_exactly_what_was_stored():
    saved = resubmit()

    assert saved.is_valid(), saved.errors
    assert saved.config() == STORED


def test_an_invalid_stored_value_left_untouched_is_kept():
    stored = dict(STORED, address="not a url", port=0, ratio=-1)
    saved = resubmit(stored)

    assert saved.is_valid(), saved.errors
    assert saved.config() == stored


def test_a_stored_list_that_is_a_string_round_trips():
    stored = {"token": "t", "recipients": "a@example.com"}
    saved = resubmit(stored)

    assert saved.is_valid(), saved.errors
    assert saved.config() == stored


def test_defaults_are_shown_but_not_written():
    shown = form(stored={"token": "t"})

    assert shown["cfg__method"].value() == "POST"
    assert shown["cfg__port"].value() == "30"
    assert shown["cfg__enabled"].value() is False

    saved = resubmit({"token": "t"})
    assert saved.is_valid(), saved.errors
    assert saved.config() == {"token": "t"}


def test_a_new_value_for_a_key_not_stored_is_written():
    saved = resubmit({"token": "t"}, cfg__port="5", cfg__enabled="on", cfg__ratio="1.5")

    assert saved.is_valid(), saved.errors
    assert saved.config() == {"token": "t", "port": 5, "enabled": True, "ratio": 1.5}


def test_an_unchecked_box_writes_false_when_it_was_stored():
    data = initial_data(form())
    del data["cfg__enabled"]
    saved = form(data)

    assert saved.is_valid(), saved.errors
    assert saved.config()["enabled"] is False


@pytest.mark.parametrize("key,bad", [("port", "0"), ("ratio", "-0.1"), ("address", "ftp://x")])
def test_a_changed_value_out_of_range_is_refused(key, bad):
    saved = resubmit(**{field_name(key): bad})

    assert not saved.is_valid()
    assert field_name(key) in saved.errors


def test_a_float_is_a_float_field_from_zero():
    field = form().fields["cfg__ratio"]

    assert isinstance(field, forms.FloatField)
    assert field.min_value == 0
    saved = resubmit(cfg__ratio="0")
    assert saved.is_valid(), saved.errors
    assert saved.config()["ratio"] == 0.0


def test_an_optional_value_left_blank_drops_its_key():
    saved = resubmit(cfg__port="", cfg__method="", cfg__recipients="", cfg__ratio="")

    assert saved.is_valid(), saved.errors
    for key in ("port", "method", "recipients", "ratio"):
        assert key not in saved.config()


def test_a_stored_choice_outside_the_list_is_offered_as_stored():
    assert ("get", "get (stored)") in form().fields["cfg__method"].choices


def test_a_choice_with_no_default_offers_not_set():
    assert form().fields["cfg__region"].choices[0] == ("", "Not set")


def test_a_list_is_one_value_per_line():
    saved = resubmit(cfg__recipients=" x@example.com \r\n\r\ny@example.com")

    assert saved.is_valid(), saved.errors
    assert saved.config()["recipients"] == ["x@example.com", "y@example.com"]


def test_headers_are_one_name_and_value_per_line():
    saved = resubmit(cfg__headers="X-One: 1\r\n\r\nX-Two:  two: parts ")

    assert saved.is_valid(), saved.errors
    assert saved.config()["headers"] == {"X-One": "1", "X-Two": "two: parts"}


@pytest.mark.parametrize("bad", ["no colon here", ": no name"])
def test_a_header_line_without_a_name_and_value_is_refused(bad):
    saved = resubmit(cfg__headers=bad)

    assert not saved.is_valid()
    assert saved.errors["cfg__headers"] == [HEADER_ERROR]


def test_a_template_keeps_its_whitespace_and_unix_newlines():
    saved = resubmit(cfg__body="  line one\r\nline two\r\n")

    assert saved.is_valid(), saved.errors
    assert saved.config()["body"] == "  line one\nline two\n"


def test_a_blank_secret_keeps_the_stored_one():
    saved = resubmit()

    assert saved["cfg__token"].value() is None
    assert saved.is_valid(), saved.errors
    assert saved.config()["token"] == "token-secret-value"


def test_a_new_secret_replaces_the_stored_one():
    saved = resubmit(cfg__token="new")

    assert saved.is_valid(), saved.errors
    assert saved.config()["token"] == "new"


def test_a_cleared_secret_is_dropped():
    saved = resubmit(cfg__headers__clear="on")

    assert saved.is_valid(), saved.errors
    assert "headers" not in saved.config()


def test_a_cleared_required_secret_is_an_error():
    saved = resubmit(cfg__token__clear="on")

    assert not saved.is_valid()
    assert saved.errors["cfg__token"] == ["Token is required."]


def test_a_secret_says_whether_it_is_set_and_offers_clear_only_then():
    shown = form()

    assert shown.fields["cfg__password"].help_text == UNSET_HELP
    assert "cfg__password__clear" not in shown.fields
    assert shown.fields["cfg__token"].help_text == f"The token. {SET_HELP}"
    assert shown.fields["cfg__token__clear"].label == "Clear"
    assert isinstance(shown.fields["cfg__token"].widget, forms.PasswordInput)


def test_an_old_spelling_of_a_secret_reads_as_set_and_clear_drops_it():
    stored = {"token": "t", "old_password": "old-name-secret", "port": 5}
    shown = form(stored=stored)

    assert shown.fields["cfg__password"].help_text == ALIAS_HELP
    assert "old-name-secret" not in str(shown)

    kept = resubmit(stored)
    assert kept.is_valid(), kept.errors
    assert kept.config() == stored

    cleared = resubmit(stored, cfg__password__clear="on")
    assert cleared.is_valid(), cleared.errors
    assert cleared.config() == {"token": "t", "port": 5}


def test_no_secret_or_unknown_value_is_ever_rendered():
    unshown = ["token-secret-value", "header-secret-value", "unknown-value"]
    pages = [str(form())]
    typed = resubmit(cfg__port="0", cfg__token="typed-secret", cfg__password="typed-secret")
    assert not typed.is_valid()
    pages.append(str(typed))

    for page in pages:
        for value in unshown:
            assert value not in page
        assert "typed-secret" not in page


def test_unknown_keys_are_listed_by_name():
    assert form().unknown_keys() == ["extra"]


def test_a_stored_config_that_is_not_a_dict_reads_as_empty():
    assert form(stored=["junk"]).unknown_keys() == []
