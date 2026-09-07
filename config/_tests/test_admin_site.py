import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory


@pytest.fixture
def su(db):
    return get_user_model().objects.create_superuser("admin", "a@b.co", "x")


def _app_list(user):
    req = RequestFactory().get("/admin/")
    req.user = user
    return admin.site.get_app_list(req)


def _sections(su):
    # Models only. The non-model pages a section also carries are asserted
    # separately by _links, so a new link cannot quietly satisfy a model test.
    return {
        a["name"]: [m["object_name"] for m in a["models"] if not m.get("is_link")]
        for a in _app_list(su)
    }


def _links(user):
    return {
        a["name"]: [m["name"] for m in a["models"] if m.get("is_link")] for a in _app_list(user)
    }


@pytest.mark.django_db
def test_sections_are_operator_facing(su):
    sections = _sections(su)
    assert set(sections) >= {"Operations", "Configuration", "History & Audit"}
    assert "Incident" in sections["Operations"]
    assert "Node" in sections["Operations"]
    assert "NotificationChannel" in sections["Configuration"]
    assert "CheckRun" in sections["History & Audit"]


@pytest.mark.django_db
def test_operations_order_is_explicit(su):
    ops = _sections(su)["Operations"]
    assert ops.index("Incident") < ops.index("Alert") < ops.index("PipelineRun")


@pytest.mark.django_db
def test_all_models_present_none_dropped(su):
    all_models = [m for models in _sections(su).values() for m in models]
    # auth models are in Configuration; nothing silently dropped
    assert "User" in all_models and "Group" in all_models
    assert "StageExecution" in all_models and "AnalysisRun" in all_models


@pytest.mark.django_db
def test_no_registered_model_is_dropped(su):
    flat = {name for models in _sections(su).values() for name in models}
    registered = {m._meta.object_name for m in admin.site._registry}
    assert registered <= flat


@pytest.mark.django_db
def test_section_map_keys_are_all_registered():
    from config.admin import SECTION_MAP

    registered = {f"{m._meta.app_label}.{m._meta.model_name}" for m in admin.site._registry}
    mapped = {k for keys in SECTION_MAP.values() for k in keys}
    assert mapped <= registered  # a typo'd or stale key fails here


@pytest.mark.django_db
def test_per_app_label_preserves_native_behaviour(su):
    # When app_label is passed (per-app index), do NOT regroup.
    req = RequestFactory().get("/admin/alerts/")
    req.user = su
    result = admin.site.get_app_list(req, app_label="alerts")
    assert [a["app_label"] for a in result] == ["alerts"]


@pytest.mark.django_db
def test_sections_respect_permissions_and_hide_empty():
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user("staff", "s@b.co", "x", is_staff=True)
    perm = Permission.objects.get(codename="view_notificationchannel")
    user.user_permissions.add(perm)
    req = RequestFactory().get("/admin/")
    req.user = user
    sections = {
        a["name"]: [m["object_name"] for m in a["models"] if not m.get("is_link")]
        for a in admin.site.get_app_list(req)
    }
    # Only Configuration (with just NotificationChannel) should appear; others hidden.
    assert "Operations" not in sections
    assert "History & Audit" not in sections
    assert sections.get("Configuration") == ["NotificationChannel"]


@pytest.mark.django_db
def test_unmapped_models_fall_into_other_section(su, monkeypatch):
    # Any registered model not listed in SECTION_MAP must surface under "Other",
    # never be silently dropped.
    from config import admin as config_admin

    partial_map = {"Operations": ["alerts.incident"]}
    monkeypatch.setattr(config_admin, "SECTION_MAP", partial_map)
    sections = _sections(su)
    assert sections["Operations"] == ["Incident"]
    assert "Other" in sections
    # Everything not mapped (e.g. Node, NotificationChannel, User) lands in Other.
    assert "Node" in sections["Other"]
    assert "NotificationChannel" in sections["Other"]
    assert "User" in sections["Other"]


@pytest.mark.django_db
def test_sections_carry_the_pages_that_are_not_changelists(su):
    links = _links(su)
    assert "Hub-side policy" in links["Operations"]
    assert "Network map" in links["Configuration"]


@pytest.mark.django_db
def test_a_link_points_at_its_page(su):
    entry = next(
        m
        for a in _app_list(su)
        for m in a["models"]
        if m.get("is_link") and m["name"] == "Hub-side policy"
    )
    assert entry["admin_url"] == "/admin/policy/"
    # Nothing to add on a page that is not a changelist.
    assert entry["add_url"] is None
    assert entry["view_only"] is True


@pytest.mark.django_db
def test_a_link_is_hidden_from_a_user_who_cannot_open_it():
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user("staff2", "s2@b.co", "x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_node"))
    links = _links(user)
    # Node view opens the policy page. Nothing here opens the map.
    assert links["Operations"] == ["Hub-side policy"]
    assert "Configuration" not in links
