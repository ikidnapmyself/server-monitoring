"""Tests for what each console write records."""

from apps.console import audited


def test_a_provider_key_stays_hidden_with_no_provider_installed(monkeypatch):
    monkeypatch.setattr(audited, "PROVIDER_SECRETS", frozenset())
    monkeypatch.setattr(audited, "PROVIDER_PUBLIC", frozenset())

    changes = audited.provider_changes({"config.api_key": "sk-old"}, {"config.api_key": "sk-new"})

    assert changes == {"config.api_key": {"changed": True}}


def test_a_policy_that_isnt_a_dict_reads_as_empty():
    assert audited.policy_changes("junk", {"cpu": {"warning_threshold": 70}}) == {
        "cpu": {"old": None, "new": {"warning_threshold": 70}}
    }


def test_turned_off_is_a_fresh_dict_each_time():
    first = audited.turned_off()
    first["is_active"]["new"] = True

    assert audited.turned_off() == {"is_active": {"old": True, "new": False}}
