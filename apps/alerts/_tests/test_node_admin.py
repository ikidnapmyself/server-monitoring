from django.contrib import admin
from django.test import TestCase

from apps.alerts.models import Node


class NodeAdminTests(TestCase):
    def test_registered_and_readonly(self):
        self.assertIn(Node, admin.site._registry)
        model_admin = admin.site._registry[Node]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertIn("instance_id", model_admin.list_display)
        self.assertIn("last_seen", model_admin.list_display)
