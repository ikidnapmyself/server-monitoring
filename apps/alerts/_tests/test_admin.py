from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

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


class TestIncidentAdminIsStock(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(self.user)
        self.incident = Incident.objects.create(subject="HighLatency", instance="db-1")

    def test_incident_admin_is_read_only_with_no_actions(self):
        model_admin = admin.site._registry[Incident]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, self.incident)
        assert not model_admin.has_delete_permission(request, self.incident)
        assert not model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_change_page_still_opens_for_a_superuser(self):
        url = reverse("admin:alerts_incident_change", args=[self.incident.pk])
        assert self.client.get(url).status_code == 200

    def test_search_matches_subject_and_instance(self):
        other = Incident.objects.create(subject="DiskFull", instance="web-1")
        for term in ("HighLatency", "db-1"):
            with self.subTest(term=term):
                html = self.client.get("/admin/alerts/incident/", {"q": term}).content.decode()
                self.assertIn(f"/admin/alerts/incident/{self.incident.pk}/change/", html)
                self.assertNotIn(f"/admin/alerts/incident/{other.pk}/change/", html)


class TestAlertAdminIsStock(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(self.user)
        self.alert = Alert.objects.create(
            fingerprint="fp",
            name="a",
            severity="warning",
            status="firing",
            source="cluster",
            started_at=timezone.now(),
        )

    def test_alert_admin_is_read_only_with_no_actions(self):
        model_admin = admin.site._registry[Alert]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, self.alert)
        assert not model_admin.has_delete_permission(request, self.alert)
        assert "resolve_selected" not in model_admin.get_actions(request)
        assert "delete_selected" not in model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_history_admin_is_read_only(self):
        model_admin = admin.site._registry[AlertHistory]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
        assert "delete_selected" not in model_admin.get_actions(request)

    def test_change_page_still_opens_for_a_superuser(self):
        url = reverse("admin:alerts_alert_change", args=[self.alert.pk])
        assert self.client.get(url).status_code == 200


class TestNodeAdminIsStock(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(self.user)
        self.node = Node.objects.create(instance_id="n1", hostname="n1")

    def test_node_admin_is_read_only_with_no_actions(self):
        model_admin = admin.site._registry[Node]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, self.node)
        assert not model_admin.has_delete_permission(request, self.node)
        assert not model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_change_page_still_opens_for_a_superuser(self):
        url = reverse("admin:alerts_node_change", args=[self.node.pk])
        assert self.client.get(url).status_code == 200

    def test_the_admin_policy_page_is_gone(self):
        assert self.client.get("/admin/policy/").status_code == 404
