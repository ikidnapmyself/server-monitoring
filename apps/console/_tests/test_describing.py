"""Tests for how an audit entry is worded."""

import pytest

from apps.console.describing import (
    ACTIONS,
    BLANK_NAME,
    KINDS,
    NOBODY,
    SECRET,
    VALUE_LIMIT,
    actor_action,
    describe_entry,
    kind_word,
)
from apps.console.models import AuditEntry
from apps.orchestration.routing import describe_match
from config.describing import Action, Fact

CLUSTER = {"field": "source", "op": "is", "value": "cluster"}


def _entry(action, kind="route", pk=7, name="web", changes=None, **fields):
    return AuditEntry(
        action=action,
        target_kind=kind,
        target_id=pk,
        target_name=name,
        changes=changes if changes is not None else {},
        **fields,
    )


def test_every_registered_action_is_worded_without_a_code():
    for action in ACTIONS:
        words = describe_entry(_entry(action), exists=True)
        assert "_" not in words.headline, action
        assert "{" not in words.headline, action
        assert "." not in words.headline, action


def test_an_edit_names_its_target_and_links_it():
    words = describe_entry(_entry("route.edit"), exists=True)

    assert words.headline == "Edited route web"
    assert (words.before, words.after) == ("Edited ", "")
    assert words.target == Action("route web", "view_route", {"pk": 7})


def test_a_target_whose_row_is_gone_is_text():
    words = describe_entry(_entry("route.edit"), exists=False)

    assert words.target == Action("route web")


def test_a_kind_with_no_page_is_text():
    assert describe_entry(_entry("x.y", kind="widget"), exists=True).target == Action("record web")


def test_an_unknown_action_falls_back_to_changed_kind_target():
    assert describe_entry(_entry("route.paint"), exists=True).headline == "Changed route web"


def test_an_unknown_kind_reads_as_a_record():
    assert kind_word("widget_box") == "record"
    assert kind_word("key") == "API key"
    assert set(KINDS) >= {"route", "channel", "provider", "key", "user", "incident", "run"}


@pytest.mark.parametrize(
    ("kind", "action", "headline"),
    [
        ("run", "run.retry", "Retried run 34"),
        ("incident", "incident.close", "Closed incident 34"),
    ],
)
def test_an_empty_name_is_worded_from_the_id(kind, action, headline):
    assert describe_entry(_entry(action, kind=kind, pk=34, name=""), exists=True).headline == (
        headline
    )


def test_an_incident_reads_by_its_subject():
    entry = _entry("incident.acknowledge", kind="incident", pk=3, name="High CPU usage")

    assert describe_entry(entry, exists=True).headline == "Acknowledged incident High CPU usage"


def test_a_node_is_named_on_read():
    named = _entry("node.reevaluate", kind="node", name="web-1")
    unnamed = _entry("node.reevaluate", kind="node", name="")

    assert describe_entry(named, exists=True).headline == (
        "Re-evaluated the open alerts of node web-1"
    )
    assert describe_entry(unnamed, exists=True).headline == (
        "Re-evaluated the open alerts of Unnamed node"
    )


def test_the_inbox_links_the_inbox_page():
    entry = _entry("inbox.drain_oldest", kind="inbox", pk=None, name="inbox")

    assert describe_entry(entry, exists=True).headline == "Drained the oldest queued runs"
    assert describe_entry(_entry("x.y", kind="inbox", pk=None), exists=True).target == Action(
        "the inbox", "view_inbox"
    )


def test_sessions_have_no_target():
    signed_in = describe_entry(_entry("session.sign_in", kind="user"), exists=True)

    assert (signed_in.headline, signed_in.target) == ("Signed in", None)
    assert describe_entry(_entry("session.sign_out"), exists=True).headline == "Signed out"


def test_a_failed_sign_in_names_what_was_typed():
    failed = _entry("session.sign_in_failed", kind="user", pk=None, name="mallory")
    blank = _entry("session.sign_in_failed", kind="user", pk=None, name="")

    assert describe_entry(failed, exists=False).headline == "Failed sign-in as mallory"
    assert describe_entry(blank, exists=False).headline == f"Failed sign-in as {BLANK_NAME}"


def test_old_and_new_render_as_a_change_with_its_field_in_words():
    changes = {
        "first_name": {"old": "", "new": "Amy"},
        "is_active": {"old": True, "new": False},
        "allowed_endpoints": {"old": [], "new": ["/alerts/", "/hub/"]},
        "channel": {"old": None, "new": "ops"},
        "config.webhook_url": {"changed": True},
        "match": {"old": None, "new": [CLUSTER]},
        "priority": {"old": 50, "new": 60},
    }

    words = describe_entry(_entry("route.edit", changes=changes), exists=True)

    assert [(c.label, c.old, c.new) for c in words.changes] == [
        ("First name", "blank", "Amy"),
        ("Active", "yes", "no"),
        ("Allowed endpoints", "none", "/alerts/, /hub/"),
        ("Channel", "not set", "ops"),
        ("Webhook URL", SECRET, SECRET),
        ("Conditions", "not set", describe_match([CLUSTER])),
        ("Priority", "50", "60"),
    ]
    assert words.summary == ""


def test_a_value_that_isnt_a_change_is_skipped():
    words = describe_entry(_entry("route.edit", changes={"odd": "x"}), exists=True)

    assert words.changes == ()


def test_changes_that_arent_a_dict_read_as_none():
    assert describe_entry(_entry("route.edit", changes=["x"]), exists=True).changes == ()


def test_a_result_renders_as_counts_in_a_sentence_not_a_change():
    changes = {"result": {"ran": 1, "failed": 2, "left": 0, "odd_count": 3, "flag": True}}

    words = describe_entry(_entry("inbox.drain", kind="run", name=""), exists=True)
    counted = describe_entry(
        _entry("inbox.drain", kind="run", name="", changes=changes), exists=True
    )

    assert words.summary == ""
    assert counted.summary == "1 run drained, 2 runs raised an error and 3 odd count."
    assert counted.changes == ()


def test_a_result_of_zeros_says_nothing_changed():
    words = describe_entry(
        _entry("incident.rerun", kind="incident", changes={"result": {"runs": 0}}), exists=True
    )

    assert words.summary == "Nothing changed."


def test_a_delete_renders_its_last_values_not_a_change():
    changes = {
        "name": {"old": "web", "new": None},
        "key": {"changed": True},
        "odd": 1,
    }

    words = describe_entry(_entry("key.delete", kind="key", changes=changes), exists=False)

    assert words.headline == "Deleted API key web"
    assert words.changes == ()
    assert words.last_values == (Fact("Name", "web"), Fact("Key", "hidden"))


def test_a_policy_change_labels_each_checker():
    changes = {
        "cpu": {
            "old": {"warning_threshold": 80},
            "new": {"warning_threshold": 70},
        }
    }

    words = describe_entry(
        _entry("node.policy", kind="node", name="web-1", changes=changes), exists=True
    )

    assert words.changes[0].label == "CPU usage"
    assert (words.changes[0].old, words.changes[0].new) == (
        "warning threshold 80",
        "warning threshold 70",
    )


def test_allowed_ports_are_the_new_allowlist_minus_the_old():
    changes = {
        "listening_ports": {
            "old": {"allowlist": [22]},
            "new": {"allowlist": [22, 80, 5432]},
        }
    }

    words = describe_entry(
        _entry("node.allow_ports", kind="node", name="web-1", changes=changes), exists=True
    )

    assert words.headline == "Allowed ports 80, 5432 on node web-1"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"listening_ports": "junk"},
        {"listening_ports": {"old": None, "new": {"allowlist": "junk"}}},
        {"listening_ports": {"old": {"allowlist": [22]}, "new": {"allowlist": [22]}}},
    ],
)
def test_allowed_ports_with_nothing_added_reads_without_ports(changes):
    words = describe_entry(
        _entry("node.allow_ports", kind="node", name="web-1", changes=changes), exists=True
    )

    assert words.headline == "Allowed ports on node web-1"


@pytest.mark.django_db
def test_the_actor_links_while_the_user_is_there(django_user_model):
    user = django_user_model.objects.create_user("amy")

    assert actor_action(AuditEntry(actor=user, actor_name="amy")) == Action(
        "amy", "view_user", {"pk": user.pk}
    )


def test_a_deleted_actor_keeps_their_name_and_nobody_reads_as_not_signed_in():
    assert actor_action(AuditEntry(actor_name="gone")) == Action("gone")
    assert actor_action(AuditEntry(actor_name="")) == Action(NOBODY)


def test_a_secret_row_is_marked_secret():
    words = describe_entry(
        _entry("channel.edit", changes={"config.webhook_url": {"changed": True}}), exists=True
    )

    assert words.changes[0].secret is True
    assert words.changes[0].old == "Changed, value hidden"


def test_config_fields_use_their_form_labels():
    changes = {
        "config.webhook_url": {"changed": True},
        "config.api_key": {"changed": True},
        "config.legacy_thing": {"old": 1, "new": 2},
    }

    words = describe_entry(_entry("channel.edit", changes=changes), exists=True)

    assert [c.label for c in words.changes] == ["Webhook URL", "API key", "Legacy thing"]


def test_the_route_and_provider_fields_have_their_own_labels():
    changes = {
        name: {"old": "a", "new": "b"}
        for name in ("driver", "provider", "stages", "email", "priority")
    }

    words = describe_entry(_entry("x.edit", kind="widget", changes=changes), exists=True)

    assert [c.label for c in words.changes] == [
        "Driver",
        "Provider",
        "Stages",
        "Email",
        "Priority",
    ]


def test_conditions_outside_a_route_are_flattened():
    changes = {"match": {"old": None, "new": [CLUSTER]}}

    words = describe_entry(_entry("x.edit", kind="widget", changes=changes), exists=True)

    assert words.changes[0].new == "field source; op is; value cluster"


def test_an_unknown_action_with_no_target_changed_a_record():
    entry = _entry("widget.spin", kind="", pk=None, name="")

    assert describe_entry(entry, exists=False).headline == "Changed a record"


def test_a_run_status_reads_as_its_label():
    changes = {"status": {"old": "failed", "new": "pending"}}
    odd = {"status": {"old": "weird", "new": None}}

    words = describe_entry(_entry("run.retry", kind="run", changes=changes), exists=True)
    odd_words = describe_entry(_entry("run.retry", kind="run", changes=odd), exists=True)

    assert (words.changes[0].old, words.changes[0].new) == ("Failed", "Pending")
    assert (odd_words.changes[0].old, odd_words.changes[0].new) == ("weird", "not set")


def test_a_long_value_is_cut_with_an_ellipsis():
    changes = {"description": {"old": "x" * 500, "new": "short"}}

    words = describe_entry(_entry("route.edit", changes=changes), exists=True)

    assert len(words.changes[0].old) == VALUE_LIMIT
    assert words.changes[0].old.endswith("\u2026")
    assert words.changes[0].new == "short"


def test_a_deleted_route_words_its_conditions():
    changes = {"match": {"old": [CLUSTER], "new": None}}

    words = describe_entry(_entry("route.delete", changes=changes), exists=False)

    assert words.last_values == (Fact("Conditions", describe_match([CLUSTER])),)
