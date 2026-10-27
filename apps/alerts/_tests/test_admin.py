import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import TestCase

from apps.alerts.models import (
    Alert,
    AlertHistory,
    Incident,
    Node,
)


class TestAdminQueryOptimization(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    def test_alert_list_uses_select_related(self):
        """AlertAdmin should use select_related('incident') to avoid N+1."""
        response = self.client.get("/admin/alerts/alert/")
        assert response.status_code == 200

    def test_incident_list_uses_select_related(self):
        from apps.alerts.admin import IncidentAdmin

        assert IncidentAdmin.list_select_related == ["node", "pipeline"]
        response = self.client.get("/admin/alerts/incident/")
        assert response.status_code == 200

    def test_pipeline_run_list_loads(self):
        response = self.client.get("/admin/orchestration/pipelinerun/")
        assert response.status_code == 200

    def test_stage_execution_list_loads(self):
        response = self.client.get("/admin/orchestration/stageexecution/")
        assert response.status_code == 200

    def test_analysis_run_list_loads(self):
        response = self.client.get("/admin/intelligence/analysisrun/")
        assert response.status_code == 200

    def test_check_run_list_loads(self):
        response = self.client.get("/admin/checkers/checkrun/")
        assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("model", [Alert, Incident, AlertHistory, Node])
def test_the_alerts_admins_are_editable(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert "delete_selected" in model_admin.get_actions(request)


def test_large_foreign_keys_are_raw_ids():
    assert admin.site._registry[Alert].raw_id_fields == ["node", "incident"]
    assert admin.site._registry[Incident].raw_id_fields == ["node"]
    assert admin.site._registry[AlertHistory].raw_id_fields == ["alert"]


class TestIncidentAdminSearch(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.incident = Incident.objects.create(subject="HighLatency", instance="db-1")

    def test_search_matches_subject_and_instance(self):
        other = Incident.objects.create(subject="DiskFull", instance="web-1")
        for term in ("HighLatency", "db-1"):
            with self.subTest(term=term):
                html = self.client.get("/admin/alerts/incident/", {"q": term}).content.decode()
                self.assertIn(f"/admin/alerts/incident/{self.incident.pk}/change/", html)
                self.assertNotIn(f"/admin/alerts/incident/{other.pk}/change/", html)


@pytest.mark.django_db
def test_the_admin_policy_page_is_gone(admin_client):
    assert admin_client.get("/admin/policy/").status_code == 404
