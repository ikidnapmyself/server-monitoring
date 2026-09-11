"""The shared admin link builders.

Every admin surface that names an object has to link it, so the four builders
here are the only place a change-page or changelist URL is spelled. These tests
pin the escaping in particular: instance_ids, checker names and channel names
all arrive over a webhook and end up as link text.
"""

import pytest
from django.urls import NoReverseMatch

from apps.alerts.models import Node
from config.admin_links import DASH, admin_link, admin_url, changelist_link, changelist_url

pytestmark = pytest.mark.django_db


def test_admin_url_points_at_the_change_page():
    node = Node.objects.create(instance_id="n1")
    assert admin_url(node) == f"/admin/alerts/node/{node.pk}/change/"


def test_admin_url_of_none_is_none():
    assert admin_url(None) is None


def test_admin_link_renders_an_anchor_labelled_with_the_object():
    node = Node.objects.create(instance_id="n1", hostname="box")
    assert admin_link(node) == f'<a href="/admin/alerts/node/{node.pk}/change/">n1 (box)</a>'


def test_admin_link_takes_an_explicit_label():
    node = Node.objects.create(instance_id="n1")
    assert admin_link(node, "3 alerts").endswith(">3 alerts</a>")


def test_admin_link_escapes_its_label():
    node = Node.objects.create(instance_id="<script>")
    assert "<script>" not in admin_link(node)
    assert "&lt;script&gt;" in admin_link(node)


def test_admin_link_of_none_is_a_dash():
    assert admin_link(None) == DASH


def test_admin_link_of_none_takes_a_replacement_for_the_dash():
    assert admin_link(None, empty="never ran") == "never ran"


def test_admin_link_on_an_unregistered_model_raises():
    """A missing registration is a programming error, not an empty cell."""

    class Unregistered:
        _meta = type("Meta", (), {"app_label": "nope", "model_name": "nope"})()
        pk = 1

    with pytest.raises(NoReverseMatch):
        admin_link(Unregistered())


def test_changelist_url_without_filters():
    assert changelist_url(Node) == "/admin/alerts/node/"


def test_changelist_url_appends_filters_in_a_stable_order():
    url = changelist_url(Node, last_source__exact="cluster", instance_id__exact="n1")
    assert url == "/admin/alerts/node/?instance_id__exact=n1&last_source__exact=cluster"


def test_changelist_url_encodes_filter_values():
    url = changelist_url(Node, instance_id__exact="a b&c")
    assert url == "/admin/alerts/node/?instance_id__exact=a+b%26c"


def test_changelist_link_renders_an_anchor():
    assert changelist_link(Node, "7", last_source__exact="local") == (
        '<a href="/admin/alerts/node/?last_source__exact=local">7</a>'
    )


def test_changelist_link_escapes_its_label():
    assert "&lt;b&gt;" in changelist_link(Node, "<b>")


def test_changelist_link_accepts_a_non_string_label():
    assert changelist_link(Node, 0) == '<a href="/admin/alerts/node/">0</a>'
