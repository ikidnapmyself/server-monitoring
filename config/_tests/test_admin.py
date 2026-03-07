"""Tests for config/admin.py — MonitoringAdminSite delegates to dashboard."""

from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from config.admin import MonitoringAdminSite


class TestMonitoringAdminSiteConfig(SimpleTestCase):
    def test_default_admin_is_monitoring_site(self):
        assert isinstance(admin.site, MonitoringAdminSite)

    def test_site_attributes(self):
        assert admin.site.site_header == "Server Monitoring"
        assert admin.site.site_title == "Server Monitoring"
        assert admin.site.index_title == "Dashboard"
        assert admin.site.index_template == "admin/dashboard.html"


class TestMonitoringAdminSiteIndex(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    @patch("config.admin.get_dashboard_context")
    def test_index_delegates_to_get_dashboard_context(self, mock_ctx):
        mock_ctx.return_value = {"test_key": "test_value"}
        response = self.client.get("/admin/")
        assert response.status_code == 200
        mock_ctx.assert_called_once()
        assert response.context["test_key"] == "test_value"
