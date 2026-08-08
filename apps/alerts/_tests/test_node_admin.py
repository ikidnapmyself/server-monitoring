from django.contrib import admin
from django.db import models as db_models
from django.test import TestCase
from django_json_widget.widgets import JSONEditorWidget

from apps.alerts.models import Node


class NodeAdminTests(TestCase):
    def _admin(self):
        self.assertIn(Node, admin.site._registry)
        return admin.site._registry[Node]

    def test_registered_with_registry_listing(self):
        model_admin = self._admin()
        self.assertIn("instance_id", model_admin.list_display)
        self.assertIn("last_seen", model_admin.list_display)

    def test_nodes_cannot_be_added_in_admin(self):
        # Nodes are created only by the ingest path.
        self.assertFalse(self._admin().has_add_permission(None))

    def test_config_is_editable_registry_is_readonly(self):
        model_admin = self._admin()
        # config is the one operator-editable field...
        self.assertIn("config", model_admin.fields)
        self.assertNotIn("config", model_admin.readonly_fields)
        # ...while ingest-owned registry fields stay read-only.
        for registry_field in ["instance_id", "hostname", "last_source", "labels"]:
            self.assertIn(registry_field, model_admin.readonly_fields)

    def test_config_uses_json_editor_widget(self):
        overrides = self._admin().formfield_overrides
        self.assertIs(overrides[db_models.JSONField]["widget"], JSONEditorWidget)
