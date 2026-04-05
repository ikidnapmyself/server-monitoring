"""Tests for the system_status management command."""

import json
import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class SystemStatusCommandTests(TestCase):
    def _call(self, *args, **kwargs):
        out = StringIO()
        err = StringIO()
        call_command("system_status", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_human_output_contains_profile(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("System Profile", output)
        self.assertIn("Role:", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_output_is_valid(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("profile", data)
        self.assertIn("pipeline", data)
        self.assertIn("definitions", data)
        self.assertIn("checks", data)
        self.assertIn("summary", data)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_checks_only_skips_dashboard(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--checks-only")
        self.assertNotIn("System Profile", output)
        self.assertIn("Consistency", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_verbose_shows_ok_checks(self, mock_read):
        mock_read.return_value = None
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        output, _ = self._call("--verbose")
        self.assertIn("OK", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_pipeline_definitions_shown(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call()
        self.assertIn("Pipeline Definitions", output)
        self.assertIn("test-pipe", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_definitions_include_stages(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertTrue(len(data["definitions"]) > 0)
        self.assertIn("stages", data["definitions"][0])

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_counts(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        summary = data["summary"]
        self.assertIn("passed", summary)
        self.assertIn("warnings", summary)
        self.assertIn("errors", summary)
