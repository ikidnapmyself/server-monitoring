import pytest
from django.http import QueryDict

from apps.notify.models import NotificationChannel
from apps.orchestration.forms import ConditionForm, RouteForm
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import save_route


def _route(**kwargs):
    defaults = {"name": "web", "stages": ["check", "notify"], "match": [], "priority": 50}
    return PipelineDefinition.objects.create(**{**defaults, **kwargs})


def _data(route=None, conditions=(), **overrides):
    data = {
        "name": route.name if route else "new-route",
        "description": "",
        "is_active": "on",
        "priority": "50",
        "channel": "",
        "stages": ["check", "notify"],
        "cond-TOTAL_FORMS": str(len(conditions)),
        "cond-INITIAL_FORMS": "0",
    }
    for i, cond in enumerate(conditions):
        for key, value in cond.items():
            data[f"cond-{i}-{key}"] = value
    data.update(overrides)
    query = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


def _condition(**data):
    form = ConditionForm(data={"label_key": "", **data})
    return form


@pytest.mark.parametrize(
    "cond, saved",
    [
        ({"field": "source", "op": "is", "value": "grafana"}, None),
        ({"field": "severity", "op": "in", "value": ["critical", "warning"]}, None),
        ({"field": "label:team", "op": "is-not", "value": "db"}, None),
        ({"field": "origin", "op": "not-in", "value": ["manual"]}, None),
        (
            {"field": "source", "value": "grafana"},
            {"field": "source", "op": "is", "value": "grafana"},
        ),
    ],
)
def test_condition_round_trips_through_initial_for(cond, saved):
    form = ConditionForm(data=ConditionForm.initial_for(cond))
    assert form.is_valid(), form.errors
    assert form.condition == (saved or cond)


def test_initial_for_shows_choice_values_in_words():
    initial = ConditionForm.initial_for(
        {"field": "severity", "op": "in", "value": ["critical", "info"]}
    )
    assert initial == {"field": "severity", "label_key": "", "op": "in", "value": "Critical, Info"}


def test_initial_for_label():
    initial = ConditionForm.initial_for({"field": "label:team", "op": "is", "value": "db"})
    assert initial == {"field": "label", "label_key": "team", "op": "is", "value": "db"}


def test_label_condition():
    form = _condition(field="label", label_key=" team ", op="is", value="db")
    assert form.is_valid()
    assert form.condition == {"field": "label:team", "op": "is", "value": "db"}


def test_label_needs_a_key():
    form = _condition(field="label", op="is", value="db")
    assert not form.is_valid()
    assert form.errors["label_key"] == ["Name the label to match on."]


def test_label_key_ignored_for_other_fields():
    form = _condition(field="source", label_key="team", op="is", value="grafana")
    assert form.is_valid()
    assert form.condition == {"field": "source", "op": "is", "value": "grafana"}


def test_membership_splits_strips_and_drops_blanks():
    form = _condition(field="source", op="in", value=" grafana, ,alertmanager ,")
    assert form.is_valid()
    assert form.condition["value"] == ["grafana", "alertmanager"]


def test_membership_needs_a_value():
    form = _condition(field="source", op="not-in", value=" , ,")
    assert not form.is_valid()
    assert form.errors["value"] == ["Give at least one value."]


def test_bad_severity_names_the_choices():
    form = _condition(field="severity", op="is", value="critcal")
    assert not form.is_valid()
    assert form.errors["value"] == [
        "Severity can't be critcal. Use one of Critical, Warning, Info."
    ]


def test_bad_membership_value_is_named():
    form = _condition(field="status", op="in", value="firing, gone")
    assert not form.is_valid()
    assert form.errors["value"] == ["Status can't be gone. Use one of Firing, Resolved."]


def test_choice_accepts_label_or_value_case_blind():
    form = _condition(field="origin", op="in", value="manual / cli, Checker_Generated")
    assert form.is_valid()
    assert form.condition["value"] == ["manual", "checker_generated"]


def test_unknown_field_is_a_choice_error():
    form = _condition(field="nope", op="is", value="x")
    assert not form.is_valid()
    assert "field" in form.errors


def test_bad_field_skips_value_check():
    form = _condition(field="nope", op="in", value="x")
    assert not form.is_valid()
    assert "value" not in form.errors


def test_labels_carry_no_code():
    form = ConditionForm()
    for bound in form:
        assert "_" not in str(bound.label)
        for _, label in getattr(bound.field, "choices", []):
            assert "_" not in str(label) and "{" not in str(label)


@pytest.mark.django_db
def test_stored_match_round_trips_through_the_form():
    match = [
        {"field": "severity", "op": "in", "value": ["critical", "warning"]},
        {"field": "label:team", "op": "is", "value": "db"},
    ]
    route = _route(match=match)
    form = RouteForm(instance=route)
    rows = [f.initial for f in form.conditions.forms]
    form = RouteForm(_data(route, rows), instance=route)
    assert form.is_valid(), (form.errors, form.conditions.errors)
    assert form.route_fields()["match"] == match


@pytest.mark.django_db
def test_route_fields_save_end_to_end():
    channel = NotificationChannel.objects.create(name="ops", driver="slack")
    route = _route()
    form = RouteForm(
        _data(
            route,
            [{"field": "source", "op": "is", "value": "grafana"}],
            channel=str(channel.pk),
            stages=["notify", "check"],
            description="Web alerts",
        ),
        instance=route,
    )
    assert form.is_valid()
    fields = form.route_fields()
    assert set(fields) == {
        "name",
        "description",
        "is_active",
        "priority",
        "channel",
        "stages",
        "match",
    }
    save_route(route.pk, fields, expected=route.version)
    route.refresh_from_db()
    assert route.channel == channel
    assert route.stages == ["check", "notify"]
    assert route.match == [{"field": "source", "op": "is", "value": "grafana"}]
    assert route.description == "Web alerts"


@pytest.mark.django_db
def test_deleted_row_is_dropped():
    route = _route()
    conditions = [
        {"field": "source", "op": "is", "value": "grafana", "DELETE": "on"},
        {"field": "source", "op": "is", "value": "zabbix"},
    ]
    form = RouteForm(_data(route, conditions), instance=route)
    assert form.is_valid()
    assert form.route_fields()["match"] == [{"field": "source", "op": "is", "value": "zabbix"}]


@pytest.mark.django_db
def test_no_rows_is_an_empty_match():
    route = _route(match=[{"field": "source", "op": "is", "value": "grafana"}])
    form = RouteForm(_data(route), instance=route)
    assert form.is_valid()
    assert form.route_fields()["match"] == []


@pytest.mark.django_db
def test_bad_row_fails_the_form():
    route = _route()
    form = RouteForm(
        _data(route, [{"field": "severity", "op": "is", "value": "bad"}]), instance=route
    )
    assert not form.is_valid()


@pytest.mark.django_db
def test_bad_form_fails_with_good_rows():
    route = _route()
    form = RouteForm(_data(route, priority="soon"), instance=route)
    assert not form.is_valid()


@pytest.mark.django_db
def test_representable_match_has_no_replace_box():
    form = RouteForm(instance=_route())
    assert "replace_match" not in form.fields


@pytest.mark.django_db
def test_unrepresentable_match_is_kept():
    weird = [{"field": "source", "op": "regex", "value": ".*"}]
    route = _route(match=weird)
    form = RouteForm(instance=route)
    assert "replace_match" in form.fields
    assert form.conditions.forms == []
    form = RouteForm(_data(route, [{"field": "source", "op": "is", "value": "x"}]), instance=route)
    assert form.is_valid()
    assert form.route_fields()["match"] == weird


@pytest.mark.django_db
def test_unrepresentable_match_is_replaced_when_ticked():
    route = _route(match={"not": "a list"})
    form = RouteForm(
        _data(route, [{"field": "source", "op": "is", "value": "x"}], replace_match="on"),
        instance=route,
    )
    assert form.is_valid()
    assert form.route_fields()["match"] == [{"field": "source", "op": "is", "value": "x"}]


def test_add_condition_bumps_the_count():
    data = QueryDict("cond-TOTAL_FORMS=2&name=x")
    bumped = RouteForm.add_condition(data)
    assert bumped["cond-TOTAL_FORMS"] == "3"
    assert bumped["name"] == "x"
    assert data["cond-TOTAL_FORMS"] == "2"


def test_add_condition_seeds_the_new_row():
    bumped = RouteForm.add_condition(QueryDict("cond-TOTAL_FORMS=2"))
    assert (bumped["cond-2-field"], bumped["cond-2-op"]) == ("source", "is")


@pytest.mark.parametrize("raw", ["", "abc"])
def test_add_condition_from_a_missing_or_bad_count(raw):
    query = f"cond-TOTAL_FORMS={raw}" if raw else ""
    bumped = RouteForm.add_condition(QueryDict(query))
    assert bumped["cond-TOTAL_FORMS"] == "1"
    assert bumped["cond-0-field"] == "source"


@pytest.mark.django_db
def test_quiet_keeps_the_values_and_drops_the_errors():
    data = RouteForm.add_condition(_data(name="", conditions=[{"value": ""}]))
    form = RouteForm(data)
    form.quiet()
    assert not form.errors
    assert not form.conditions.non_form_errors()
    assert all(not row.errors for row in form.conditions.forms)
    assert len(form.conditions.forms) == 2
    assert form["priority"].value() == "50"


@pytest.mark.django_db
def test_a_taken_name_says_so_plainly():
    _route(name="taken")
    form = RouteForm(_data(name="taken"))
    assert not form.is_valid()
    assert form.errors["name"] == ["Another route already has this name."]


@pytest.mark.django_db
def test_the_route_fields_read_plainly():
    form = RouteForm()
    assert form["is_active"].label == "On"
    assert form.fields["name"].help_text == "No two routes share a name."
    assert form.fields["description"].help_text == "What this route is for."
    assert form.fields["is_active"].help_text == (
        "A route that's off matches nothing. Its traffic goes to the next route that matches."
    )
    assert form.fields["priority"].help_text == (
        "Lower goes first, and the first route that matches wins. A new route starts at 100, "
        "and the seeded catch-all sits at 1000, so it only takes what no other route matched."
    )
    assert form.fields["channel"].empty_label == "No channel"
    for bound in form:
        assert "_" not in str(bound.help_text) and "{" not in str(bound.help_text)


def test_the_priority_help_names_the_real_numbers():
    from apps.orchestration import seeding
    from apps.orchestration.forms import CATCH_ALL_PRIORITY, DEFAULT_PRIORITY

    catch_all = next(lane for lane in seeding._LANES if lane["name"] == "catch-all")
    assert CATCH_ALL_PRIORITY == catch_all["priority"]
    assert DEFAULT_PRIORITY == PipelineDefinition().priority


@pytest.mark.django_db
def test_stages_come_back_in_canonical_order():
    route = _route()
    form = RouteForm(_data(route, stages=["notify", "analyze", "check"]), instance=route)
    assert form.is_valid()
    assert form.route_fields()["stages"] == ["check", "analyze", "notify"]


@pytest.mark.django_db
def test_no_stages_is_allowed():
    route = _route()
    form = RouteForm(_data(route, stages=[]), instance=route)
    assert form.is_valid()
    assert form.route_fields()["stages"] == []


@pytest.mark.django_db
def test_junk_stored_stages_dont_block_a_save():
    route = _route(stages=["notify", "bogus"])
    form = RouteForm(_data(route), instance=route)
    assert form.is_valid(), form.errors
    assert form.route_fields()["stages"] == ["check", "notify"]


@pytest.mark.django_db
def test_stage_initial_is_stored_stages():
    route = _route(stages=["notify", "check", "bogus"])
    assert RouteForm(instance=route)["stages"].initial == ["check", "notify"]


@pytest.mark.django_db
def test_new_route_starts_with_every_stage():
    form = RouteForm()
    assert form["stages"].initial == ["check", "analyze", "notify"]
    assert form.conditions.forms == []


@pytest.mark.django_db
def test_stage_choices_are_words():
    assert list(RouteForm().fields["stages"].choices) == [
        ("check", "Diagnose"),
        ("analyze", "Analyze"),
        ("notify", "Notify"),
    ]


@pytest.mark.django_db
def test_new_route_validates_and_names_must_be_unique():
    _route(name="taken")
    form = RouteForm(_data(name="taken"))
    assert not form.is_valid()
    assert "name" in form.errors
    form = RouteForm(_data(name="fresh"))
    assert form.is_valid()
    PipelineDefinition.objects.create(**form.route_fields())
    assert PipelineDefinition.objects.filter(name="fresh").exists()


@pytest.mark.django_db
def test_disabled_disables_everything():
    route = _route(
        match=[{"field": "source", "op": "is", "value": "x"}, {"field": "bad"}],
    )
    form = RouteForm(instance=route, disabled=True)
    assert all(f.disabled for f in form.fields.values())
    assert "replace_match" in form.fields
    route = _route(name="other", match=[{"field": "source", "op": "is", "value": "x"}])
    form = RouteForm(instance=route, disabled=True)
    rows = form.conditions.forms
    assert rows
    assert all(f.disabled for row in rows for f in row.fields.values())


@pytest.mark.django_db
def test_inactive_channel_reads_off():
    NotificationChannel.objects.create(name="ops", driver="slack")
    NotificationChannel.objects.create(name="old", driver="slack", is_active=False)
    field = RouteForm().fields["channel"]
    labels = [label for _, label in field.choices]
    assert "ops" in labels
    assert "old (off)" in labels
    assert field.label == "Channel"
    assert field.required is False


def test_bad_origin_names_the_choices():
    form = _condition(field="origin", op="is", value="x")
    assert not form.is_valid()
    assert form.errors["value"] == [
        "Started by can't be x. Use one of Incoming webhook, Checker generated, Manual / CLI."
    ]


def test_op_is_labelled_rule():
    assert ConditionForm().fields["op"].label == "Rule"


def test_blank_row_is_valid_with_no_condition():
    form = _condition(field="label", op="is", value=" ")
    assert form.is_valid()
    assert form.condition is None


def test_label_key_without_value_is_an_error():
    form = _condition(field="label", label_key="team", op="is", value="")
    assert not form.is_valid()
    assert form.errors["value"] == ["This field is required."]


@pytest.mark.django_db
def test_an_added_blank_row_is_dropped():
    route = _route()
    rows = [
        {"field": "source", "op": "is", "value": "zabbix"},
        {"field": "source", "op": "is", "value": "", "label_key": ""},
    ]
    form = RouteForm(_data(route, rows), instance=route)
    assert form.is_valid(), form.conditions.errors
    assert form.route_fields()["match"] == [{"field": "source", "op": "is", "value": "zabbix"}]


@pytest.mark.django_db
def test_a_row_with_a_key_and_no_value_fails_the_form():
    route = _route()
    data = _data(route, [{"field": "label", "label_key": "team", "op": "is", "value": ""}])
    form = RouteForm(data, instance=route)
    assert not form.is_valid()
    assert form.conditions.errors[0]["value"] == ["This field is required."]


@pytest.mark.django_db
def test_round_trip_in_the_shape_the_page_posts():
    match = [
        {"field": "status", "op": "is", "value": "resolved"},
        {"field": "label:env", "op": "not-in", "value": ["dev", "test"]},
    ]
    route = _route(match=match)
    rows = [f.initial for f in RouteForm(instance=route).conditions.forms]
    form = RouteForm(_data(route, rows, **{"cond-INITIAL_FORMS": str(len(rows))}), instance=route)
    assert form.is_valid(), form.conditions.errors
    assert form.route_fields()["match"] == match


@pytest.mark.django_db
def test_a_match_the_rows_cant_reproduce_is_kept_on_save():
    stored = [{"field": "source", "op": "in", "value": ["a,b", "c"]}]
    route = _route(match=stored)
    form = RouteForm(instance=route)
    assert "replace_match" in form.fields
    assert form.conditions.forms == []
    form = RouteForm(_data(route, description="edited"), instance=route)
    assert form.is_valid()
    save_route(route.pk, form.route_fields(), expected=route.version)
    route.refresh_from_db()
    assert route.match == stored
    assert route.description == "edited"
