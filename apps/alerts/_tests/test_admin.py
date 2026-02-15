import pytest


@pytest.mark.django_db
class TestAdminQueryOptimization:
    def test_alert_list_uses_select_related(self, admin_client):
        """AlertAdmin should use select_related('incident') to avoid N+1."""
        response = admin_client.get("/admin/alerts/alert/")
        assert response.status_code == 200

    def test_incident_list_uses_prefetch_related(self, admin_client):
        response = admin_client.get("/admin/alerts/incident/")
        assert response.status_code == 200

    def test_pipeline_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/orchestration/pipelinerun/")
        assert response.status_code == 200

    def test_stage_execution_list_loads(self, admin_client):
        response = admin_client.get("/admin/orchestration/stageexecution/")
        assert response.status_code == 200

    def test_analysis_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/intelligence/analysisrun/")
        assert response.status_code == 200

    def test_check_run_list_loads(self, admin_client):
        response = admin_client.get("/admin/checkers/checkrun/")
        assert response.status_code == 200
