import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.console.queries.channels import (
    LaneLine,
    UnknownDriver,
    build_channel_deletion,
    build_channel_editor,
    build_channel_list,
    driver_choices,
)
from apps.notify.channels import channel_digest
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes
from config.describing import Action

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


def _channel(name, driver="slack", **kw):
    return NotificationChannel.objects.create(name=name, driver=driver, **kw)


def test_rows_are_ordered_by_name_and_filtered_case_insensitively():
    _channel("zeta")
    _channel("Ops-Mail", driver="email")
    _channel("ops-slack")

    names = [row.name for row in build_channel_list("OPS").rows]

    assert names == ["Ops-Mail", "ops-slack"]
    assert [row.name for row in build_channel_list("").rows] == ["Ops-Mail", "ops-slack", "zeta"]


def test_a_row_names_its_driver_state_and_edit_action():
    ch = _channel("ops", is_active=False)
    pigeon = _channel("pigeon", driver="carrier_pigeon")

    rows = {row.name: row for row in build_channel_list("").rows}

    assert rows["ops"].driver_label == "Slack"
    assert rows["ops"].active is False
    assert rows["ops"].edit == Action("ops", "view_channel", {"pk": ch.pk})
    assert rows["ops"].anchor == f"channel-{ch.pk}"
    assert rows["pigeon"].driver_label == "A driver this hub doesn't have"
    assert pigeon.pk == rows["pigeon"].pk


def test_the_list_carries_q_and_the_add_action():
    channels = build_channel_list("ops")

    assert channels.q == "ops"
    assert channels.add == Action("Add a channel", "add_channel", {})


def test_lanes_name_their_gap_and_link_their_route():
    ops = _channel("ops")
    pigeon = _channel("pigeon", driver="carrier_pigeon")
    late = PipelineDefinition.objects.create(
        name="late", priority=20, stages=["notify"], channel=ops
    )
    early = PipelineDefinition.objects.create(
        name="early", priority=10, stages=["check"], channel=ops
    )
    pager = PipelineDefinition.objects.create(
        name="pager", priority=10, stages=["notify"], channel=pigeon
    )

    rows = {row.name: row for row in build_channel_list("").rows}

    assert rows["ops"].lanes == (
        LaneLine("early", "", Action("early", "view_route", {"pk": early.pk})),
        LaneLine("late", "", Action("late", "view_route", {"pk": late.pk})),
    )
    assert rows["pigeon"].lanes == (
        LaneLine(
            "pager",
            "the channel uses a driver this hub doesn't have",
            Action("pager", "view_route", {"pk": pager.pk}),
        ),
    )


def test_the_lanes_come_from_one_query():
    for i in range(3):
        ch = _channel(f"ch-{i}")
        PipelineDefinition.objects.create(name=f"lane-{i}", stages=["notify"], channel=ch)

    with CaptureQueriesContext(connection) as queries:
        build_channel_list("")

    assert len(queries) == 2


def test_driver_choices_follow_the_registry():
    choices = driver_choices()

    assert [c.key for c in choices] == list(DRIVER_REGISTRY)
    assert choices[0].label == DRIVER_REGISTRY[choices[0].key].label
    assert choices[0].url == f"/console/channels/new/?driver={choices[0].key}"


class TestTheEditor:
    def test_a_missing_channel_is_none(self):
        assert build_channel_editor(999) is None

    def test_a_missing_channel_wins_over_a_bad_driver(self):
        assert build_channel_editor(999, driver="nope") is None

    def test_an_existing_channel(self):
        ch = _channel("ops", config={"webhook_url": "https://hooks.example.com/x", "extra": 1})
        PipelineDefinition.objects.create(name="web", stages=["notify"], channel=ch)

        editor = build_channel_editor(ch.pk)

        row = NotificationChannel.objects.filter(pk=ch.pk).values(
            "name", "driver", "is_active", "description", "config"
        )[0]
        assert editor.digest == channel_digest(row)
        assert editor.url == f"/console/channels/{ch.pk}/?driver=slack"
        assert editor.back_url == f"/console/channels/#channel-{ch.pk}"
        assert [line.name for line in editor.lanes] == ["web"]
        labels = [fact.label for fact in editor.facts]
        assert labels == ["Driver", "Settings this driver doesn't declare", "Created", "Updated"]
        assert editor.facts[1].value == "extra"

    def test_no_unknown_settings_leaves_that_fact_out(self):
        ch = _channel("ops")

        editor = build_channel_editor(ch.pk)

        assert [fact.label for fact in editor.facts] == ["Driver", "Created", "Updated"]
        assert editor.lanes == ()

    def test_a_carried_digest_is_kept(self):
        ch = _channel("ops")

        assert build_channel_editor(ch.pk, digest="old").digest == "old"

    def test_a_new_channel(self):
        editor = build_channel_editor(None, driver="email")

        assert (editor.pk, editor.digest, editor.facts, editor.lanes) == (None, "", (), ())
        assert editor.url == "/console/channels/new/?driver=email"
        assert editor.back_url == "/console/channels/"
        assert editor.form.driver_key == "email"

    @pytest.mark.parametrize("driver", ["", "nope"])
    def test_a_new_channel_needs_a_registered_driver(self, driver):
        with pytest.raises(UnknownDriver):
            build_channel_editor(None, driver=driver)

    def test_an_existing_channel_refuses_a_driver_it_isnt_on(self):
        ch = _channel("ops")

        with pytest.raises(UnknownDriver):
            build_channel_editor(ch.pk, driver="carrier_pigeon")

    def test_a_channel_on_an_unknown_driver_keeps_it(self):
        ch = _channel("pigeon", driver="carrier_pigeon")

        editor = build_channel_editor(ch.pk, driver="carrier_pigeon")

        assert editor.form.driver_label == "A driver this hub doesn't have"

    def test_carried_fields_open_the_form_unbound(self):
        ch = _channel("ops")

        editor = build_channel_editor(ch.pk, driver="email", carried={"name": "renamed"})

        assert not editor.form.is_bound
        assert editor.form.initial["name"] == "renamed"
        assert editor.form.driver_key == "email"


class TestTheDeletion:
    def test_a_missing_channel_is_none(self):
        assert build_channel_deletion(999) is None

    def test_it_names_only_the_lanes_that_notify(self):
        ch = _channel("ops")
        web = PipelineDefinition.objects.create(name="web", stages=["notify"], channel=ch)
        PipelineDefinition.objects.create(name="quiet", stages=["check"], channel=ch)

        deletion = build_channel_deletion(ch.pk)

        assert deletion.name == "ops"
        assert deletion.cut == (Action("web", "view_route", {"pk": web.pk}),)
        assert deletion.url == f"/console/channels/{ch.pk}/delete/"
        assert deletion.back_url == f"/console/channels/{ch.pk}/"

    def test_the_editor_offers_it_only_for_an_existing_channel(self):
        ch = _channel("ops")

        existing = build_channel_editor(ch.pk)

        assert existing.delete == Action("Delete channel", "delete_channel", {"pk": ch.pk})
        assert build_channel_editor(None, driver="slack").delete is None


@pytest.mark.parametrize(
    ("active", "driver", "tone"),
    [(True, "slack", "ok"), (False, "slack", "muted"), (True, "carrier_pigeon", "critical")],
)
def test_a_row_tone_puts_a_lane_that_cant_deliver_first(active, driver, tone):
    ch = _channel("ops", driver=driver, is_active=active)
    PipelineDefinition.objects.create(name="early", stages=["check"], channel=ch)
    if driver != "slack":
        PipelineDefinition.objects.create(name="pager", stages=["notify"], channel=ch)

    assert build_channel_list("").rows[0].tone == tone
