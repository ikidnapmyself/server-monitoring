"""Tests for the show_pipeline management command."""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.orchestration.models import PipelineDefinition


class ShowPipelineListTests(TestCase):
    """Tests for listing pipeline definitions."""

    def test_empty_state_shows_warning(self):
        """Empty state shows 'No pipeline definitions found'."""
        out = StringIO()
        err = StringIO()
        call_command("show_pipeline", stdout=out, stderr=err)
        self.assertIn("No pipeline definitions found", err.getvalue())

    def test_lists_active_pipelines(self):
        """Lists active pipelines."""
        PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check_health",
                        "type": "context",
                        "config": {"checker_names": ["cpu"]},
                        "next": "notify_channels",
                    },
                    {
                        "id": "notify_channels",
                        "type": "notify",
                        "config": {"drivers": ["slack"]},
                    },
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", stdout=out)
        output = out.getvalue()
        self.assertIn("full", output)

    def test_excludes_inactive_by_default(self):
        """Excludes inactive pipelines by default."""
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        out = StringIO()
        err = StringIO()
        call_command("show_pipeline", stdout=out, stderr=err)
        self.assertNotIn("old", out.getvalue())
        self.assertIn("No pipeline definitions found", err.getvalue())

    def test_all_flag_includes_inactive(self):
        """--all flag includes inactive pipelines with (inactive) marker."""
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        out = StringIO()
        call_command("show_pipeline", "--all", stdout=out)
        output = out.getvalue()
        self.assertIn("old", output)
        self.assertIn("(inactive)", output)


class ShowPipelineSingleTests(TestCase):
    """Tests for showing a specific pipeline by name."""

    def test_name_shows_specific_pipeline(self):
        """--name shows a specific pipeline."""
        PipelineDefinition.objects.create(
            name="my-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {"drivers": ["slack"]},
                    },
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", "--name", "my-pipeline", stdout=out)
        output = out.getvalue()
        self.assertIn("my-pipeline", output)

    def test_name_not_found_shows_error(self):
        """--name with non-existent pipeline shows error."""
        out = StringIO()
        err = StringIO()
        call_command("show_pipeline", "--name", "nonexistent", stdout=out, stderr=err)
        self.assertIn("not found", err.getvalue())


class ShowPipelineJsonTests(TestCase):
    """Tests for JSON output."""

    def test_json_list_output(self):
        """--json list output is a valid JSON array."""
        PipelineDefinition.objects.create(
            name="alpha",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {"drivers": ["email"]},
                    },
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "alpha")

    def test_json_single_output(self):
        """--json --name output is a valid JSON object."""
        PipelineDefinition.objects.create(
            name="beta",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check",
                        "type": "context",
                        "config": {"checker_names": ["cpu", "memory"]},
                        "next": "notify",
                    },
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {"drivers": ["slack"]},
                    },
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", "--name", "beta", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], "beta")
        self.assertEqual(data["checkers"], ["cpu", "memory"])

    def test_json_not_found_output(self):
        """--json --name with non-existent pipeline returns JSON with error key."""
        out = StringIO()
        call_command("show_pipeline", "--name", "missing", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIn("error", data)
        self.assertEqual(data["error"], "not_found")
        self.assertEqual(data["name"], "missing")

    def test_json_empty_list(self):
        """--json with no pipelines returns []."""
        out = StringIO()
        call_command("show_pipeline", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data, [])
