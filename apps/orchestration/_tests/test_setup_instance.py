"""Tests for the setup_instance management command."""

import os
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.notify.models import NotificationChannel
from apps.orchestration.management.commands.setup_instance import Command
from apps.orchestration.models import PipelineDefinition


class PromptChoiceTests(TestCase):
    """Tests for _prompt_choice helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="2")
    def test_returns_selected_option(self, _mock_input):
        result = self.cmd._prompt_choice(
            "Pick one:", [("a", "Option A"), ("b", "Option B"), ("c", "Option C")]
        )
        assert result == "b"

    @patch("builtins.input", side_effect=["0", "5", "2"])
    def test_retries_on_invalid_input(self, _mock_input):
        result = self.cmd._prompt_choice("Pick one:", [("a", "Option A"), ("b", "Option B")])
        assert result == "b"

    @patch("builtins.input", side_effect=["abc", "1"])
    def test_retries_on_non_numeric_input(self, _mock_input):
        result = self.cmd._prompt_choice("Pick one:", [("a", "Option A")])
        assert result == "a"


class PromptMultiTests(TestCase):
    """Tests for _prompt_multi helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,3")
    def test_returns_selected_options(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B"), ("c", "C")])
        assert result == ["a", "c"]

    @patch("builtins.input", return_value="1, 2, 3")
    def test_handles_spaces_in_input(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B"), ("c", "C")])
        assert result == ["a", "b", "c"]

    @patch("builtins.input", side_effect=["", "1"])
    def test_retries_on_empty_input(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B")])
        assert result == ["a"]


class PromptInputTests(TestCase):
    """Tests for _prompt_input helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="hello")
    def test_returns_user_input(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:")
        assert result == "hello"

    @patch("builtins.input", return_value="")
    def test_returns_default_when_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", default="fallback")
        assert result == "fallback"

    @patch("builtins.input", side_effect=["", "val"])
    def test_retries_when_required_and_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", required=True)
        assert result == "val"


class SelectAlertSourceTests(TestCase):
    """Tests for _select_alert_source step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_select_external(self, _mock_input):
        result = self.cmd._select_alert_source()
        assert result == "external"

    @patch("builtins.input", return_value="2")
    def test_select_local(self, _mock_input):
        result = self.cmd._select_alert_source()
        assert result == "local"


class SelectPresetTests(TestCase):
    """Tests for _select_preset step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_select_direct_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="external")
        assert preset["name"] == "direct"
        assert preset["has_alerts"] is True
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="4")
    def test_select_full_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="external")
        assert preset["name"] == "full"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is True

    @patch("builtins.input", return_value="2")
    def test_select_health_checked_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="external")
        assert preset["name"] == "health-checked"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="3")
    def test_select_ai_analyzed_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="external")
        assert preset["name"] == "ai-analyzed"
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is True

    @patch("builtins.input", return_value="1")
    def test_select_local_monitor_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="local")
        assert preset["name"] == "local-monitor"
        assert preset["has_alerts"] is False
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="2")
    def test_select_local_smart_preset(self, _mock_input):
        preset = self.cmd._select_preset(source="local")
        assert preset["name"] == "local-smart"
        assert preset["has_alerts"] is False
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is True


class ConfigureAlertsTests(TestCase):
    """Tests for _configure_alerts step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,2,8")
    def test_returns_selected_drivers(self, _mock_input):
        result = self.cmd._configure_alerts()
        assert result == ["alertmanager", "grafana", "generic"]

    @patch("builtins.input", return_value="1")
    def test_single_driver_selection(self, _mock_input):
        result = self.cmd._configure_alerts()
        assert result == ["alertmanager"]


class ConfigureCheckersTests(TestCase):
    """Tests for _configure_checkers step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,2")
    def test_returns_selected_checkers(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "cpu" in result["enabled"]
        assert "memory" in result["enabled"]

    @patch("builtins.input", side_effect=["1,2,3", "/,/home"])
    def test_disk_checker_asks_for_paths(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "disk" in result["enabled"]
        assert result["disk_paths"] == "/,/home"

    @patch("builtins.input", side_effect=["7", "8.8.8.8"])
    def test_network_checker_asks_for_hosts(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "network" in result["enabled"]
        assert result["network_hosts"] == "8.8.8.8"

    @patch("builtins.input", side_effect=["8", "nginx,postgres"])
    def test_process_checker_asks_for_names(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "process" in result["enabled"]
        assert result["process_names"] == "nginx,postgres"


class ConfigureIntelligenceTests(TestCase):
    """Tests for _configure_intelligence step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_local_provider_needs_no_config(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["provider"] == "local"
        assert "api_key" not in result

    @patch("builtins.input", side_effect=["2", "sk-test123", "gpt-4o-mini"])
    def test_openai_provider_collects_credentials(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["provider"] == "openai"
        assert result["api_key"] == "sk-test123"
        assert result["model"] == "gpt-4o-mini"

    @patch("builtins.input", side_effect=["2", "sk-test123", ""])
    def test_openai_uses_default_model(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["model"] == "gpt-4o-mini"


class ConfigureNotifyTests(TestCase):
    """Tests for _configure_notify step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch(
        "builtins.input",
        side_effect=["1", "https://hooks.slack.com/xxx", "ops-alerts"],
    )
    def test_slack_collects_webhook_url_and_name(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "slack"
        assert result[0]["config"]["webhook_url"] == "https://hooks.slack.com/xxx"
        assert result[0]["name"] == "ops-alerts"

    @patch(
        "builtins.input",
        side_effect=[
            "3",  # email is 3rd in registry: slack, pagerduty, email, generic
            "smtp.example.com",
            "587",
            "user@example.com",
            "password123",
            "noreply@example.com",
            "ops@example.com",
            "ops-email",
        ],
    )
    def test_email_collects_smtp_settings(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["config"]["smtp_host"] == "smtp.example.com"
        assert result[0]["config"]["smtp_port"] == "587"

    @patch(
        "builtins.input",
        side_effect=["2", "R0123456789", "oncall-pd"],
    )
    def test_pagerduty_collects_routing_key(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["config"]["routing_key"] == "R0123456789"

    @patch(
        "builtins.input",
        side_effect=["4", "https://example.com/hook", "", "my-webhook"],
    )
    def test_generic_collects_endpoint(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "generic"
        assert result[0]["config"]["endpoint_url"] == "https://example.com/hook"


class ShowSummaryTests(TestCase):
    """Tests for _show_summary step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_summary_includes_preset_name(self):
        config = {
            "preset": {"name": "full", "label": "Full pipeline"},
            "alerts": ["alertmanager", "grafana"],
            "checkers": {"enabled": ["cpu", "memory"]},
            "intelligence": {"provider": "openai", "model": "gpt-4o-mini"},
            "notify": [{"driver": "slack", "name": "ops-alerts"}],
        }
        self.cmd._show_summary(config)
        output = self.cmd.stdout.getvalue()
        assert "full" in output.lower() or "Full pipeline" in output

    def test_summary_includes_all_drivers(self):
        config = {
            "preset": {"name": "direct", "label": "Direct"},
            "alerts": ["grafana"],
            "notify": [{"driver": "slack", "name": "ops-slack"}],
        }
        self.cmd._show_summary(config)
        output = self.cmd.stdout.getvalue()
        assert "grafana" in output
        assert "slack" in output


class ConfirmApplyTests(TestCase):
    """Tests for _confirm_apply step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="Y")
    def test_returns_true_on_yes(self, _mock_input):
        assert self.cmd._confirm_apply() is True

    @patch("builtins.input", return_value="")
    def test_returns_true_on_empty_default_yes(self, _mock_input):
        assert self.cmd._confirm_apply() is True

    @patch("builtins.input", return_value="n")
    def test_returns_false_on_no(self, _mock_input):
        assert self.cmd._confirm_apply() is False


class WriteEnvTests(TestCase):
    """Tests for _write_env helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_adds_new_keys_to_env_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("EXISTING_KEY=value\n")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"NEW_KEY": "new_value"})
            with open(env_path) as f:
                content = f.read()
            assert "EXISTING_KEY=value" in content
            assert "NEW_KEY=new_value" in content
        finally:
            os.unlink(env_path)

    def test_updates_existing_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("MY_KEY=old\nOTHER=keep\n")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"MY_KEY": "new"})
            with open(env_path) as f:
                content = f.read()
            assert "MY_KEY=new" in content
            assert "MY_KEY=old" not in content
            assert "OTHER=keep" in content
        finally:
            os.unlink(env_path)

    def test_creates_env_file_if_missing(self):
        env_path = tempfile.mktemp(suffix=".env")
        try:
            self.cmd._write_env(env_path, {"KEY": "val"})
            with open(env_path) as f:
                content = f.read()
            assert "KEY=val" in content
        finally:
            if os.path.exists(env_path):
                os.unlink(env_path)

    def test_adds_section_header_comment(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"KEY": "val"})
            with open(env_path) as f:
                content = f.read()
            assert "setup_instance" in content
        finally:
            os.unlink(env_path)


class CreatePipelineDefinitionTests(TestCase):
    """Tests for _create_pipeline_definition."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_creates_direct_pipeline(self):
        config = {
            "preset": {
                "name": "direct",
                "has_alerts": True,
                "has_checkers": False,
                "has_intelligence": False,
            },
            "alerts": ["grafana"],
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        assert defn.name == "direct"
        assert defn.is_active is True
        assert "setup_wizard" in defn.tags
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert "ingest" in node_types
        assert "notify" in node_types
        assert "context" not in node_types
        assert "intelligence" not in node_types

    def test_creates_full_pipeline(self):
        config = {
            "preset": {
                "name": "full",
                "has_alerts": True,
                "has_checkers": True,
                "has_intelligence": True,
            },
            "alerts": ["alertmanager"],
            "checkers": {"enabled": ["cpu", "memory"]},
            "intelligence": {"provider": "openai"},
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

    def test_nodes_are_chained_with_next(self):
        config = {
            "preset": {
                "name": "full",
                "has_alerts": True,
                "has_checkers": True,
                "has_intelligence": True,
            },
            "alerts": ["alertmanager"],
            "checkers": {"enabled": ["cpu"]},
            "intelligence": {"provider": "local"},
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        nodes = defn.get_nodes()
        # Each node except last should have "next" pointing to next node
        for i, node in enumerate(nodes[:-1]):
            assert node["next"] == nodes[i + 1]["id"]
        assert "next" not in nodes[-1]

    def test_creates_local_pipeline_without_ingest(self):
        config = {
            "preset": {
                "name": "local-monitor",
                "has_alerts": False,
                "has_checkers": True,
                "has_intelligence": False,
            },
            "checkers": {"enabled": ["cpu", "memory"]},
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert "ingest" not in node_types
        assert node_types == ["context", "notify"]

    def test_tags_include_setup_wizard(self):
        config = {
            "preset": {
                "name": "direct",
                "has_alerts": True,
                "has_checkers": False,
                "has_intelligence": False,
            },
            "alerts": ["generic"],
            "notify": [{"driver": "generic", "name": "wh", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        assert "setup_wizard" in defn.tags


class CreateNotificationChannelsTests(TestCase):
    """Tests for _create_notification_channels."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_creates_channel_records(self):
        channels_config = [
            {
                "driver": "slack",
                "name": "ops-slack",
                "config": {"webhook_url": "https://hooks.slack.com/xxx"},
            },
        ]
        channels = self.cmd._create_notification_channels(channels_config)
        assert len(channels) == 1
        ch = NotificationChannel.objects.get(name="ops-slack")
        assert ch.driver == "slack"
        assert ch.config["webhook_url"] == "https://hooks.slack.com/xxx"
        assert ch.is_active is True
        assert "[setup_wizard]" in ch.description

    def test_creates_multiple_channels(self):
        channels_config = [
            {"driver": "slack", "name": "slack-ch", "config": {}},
            {"driver": "email", "name": "email-ch", "config": {}},
        ]
        channels = self.cmd._create_notification_channels(channels_config)
        assert len(channels) == 2
        assert NotificationChannel.objects.count() == 2


class DetectExistingTests(TestCase):
    """Tests for _detect_existing."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_returns_none_when_no_existing(self):
        result = self.cmd._detect_existing()
        assert result is None

    def test_returns_definition_when_exists(self):
        PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        result = self.cmd._detect_existing()
        assert result is not None
        assert result.name == "full"

    def test_ignores_non_wizard_definitions(self):
        PipelineDefinition.objects.create(
            name="custom",
            config={"version": "1.0", "nodes": []},
            tags=["manual"],
            created_by="admin",
        )
        result = self.cmd._detect_existing()
        assert result is None


class HandleRerunTests(TestCase):
    """Tests for _handle_rerun."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_reconfigure_deactivates_existing(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={},
            description="[setup_wizard] slack channel",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "reconfigure"
        defn.refresh_from_db()
        assert defn.is_active is False
        ch = NotificationChannel.objects.get(name="ops-slack")
        assert ch.is_active is False

    @patch("builtins.input", return_value="2")
    def test_add_another_keeps_existing(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "add"
        defn.refresh_from_db()
        assert defn.is_active is True

    @patch("builtins.input", return_value="3")
    def test_cancel_returns_cancel(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "cancel"


class SetupInstanceIntegrationTests(TestCase):
    """Integration tests for the full setup_instance flow."""

    @patch(
        "apps.orchestration.management.commands.setup_instance.Command._write_env",
        return_value=None,
    )
    @patch(
        "builtins.input",
        side_effect=[
            "1",  # alert source: external
            "4",  # preset: full
            "1,2",  # alerts: alertmanager, grafana
            "1,2",  # checkers: cpu, memory
            "1",  # intelligence: local
            "1",  # notify: slack (1st in registry)
            "https://hooks.slack.com/xxx",  # slack webhook
            "ops-alerts",  # channel name
            "Y",  # confirm
        ],
    )
    def test_full_pipeline_flow(self, _mock_input, _mock_write_env):
        out = StringIO()
        call_command("setup_instance", stdout=out)

        # Verify PipelineDefinition created
        defn = PipelineDefinition.objects.get(created_by="setup_instance")
        assert defn.is_active is True
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

        # Verify NotificationChannel created
        ch = NotificationChannel.objects.get(name="ops-alerts")
        assert ch.driver == "slack"
        assert ch.is_active is True

    @patch(
        "apps.orchestration.management.commands.setup_instance.Command._write_env",
        return_value=None,
    )
    @patch(
        "builtins.input",
        side_effect=[
            "1",  # alert source: external
            "1",  # preset: direct
            "1",  # alerts: first driver
            "1",  # notify: slack
            "https://hooks.slack.com/xxx",  # slack webhook
            "ops-slack",  # channel name
            "Y",  # confirm
        ],
    )
    def test_direct_preset_skips_checkers_and_intelligence(self, _mock_input, _mock_write_env):
        out = StringIO()
        call_command("setup_instance", stdout=out)

        defn = PipelineDefinition.objects.get(created_by="setup_instance")
        node_types = [n["type"] for n in defn.get_nodes()]
        assert "context" not in node_types
        assert "intelligence" not in node_types

    @patch(
        "apps.orchestration.management.commands.setup_instance.Command._write_env",
        return_value=None,
    )
    @patch(
        "builtins.input",
        side_effect=[
            "1",  # alert source: external
            "1",  # preset: direct
            "1",  # alerts
            "1",  # notify: slack
            "https://example.com",  # slack webhook
            "test-ch",  # channel name
            "n",  # cancel at confirmation
        ],
    )
    def test_cancel_on_confirmation_creates_nothing(self, _mock_input, _mock_write_env):
        """When user cancels at confirmation, no DB records should be created."""
        out = StringIO()
        call_command("setup_instance", stdout=out)
        assert PipelineDefinition.objects.count() == 0
        assert NotificationChannel.objects.count() == 0

    @patch(
        "apps.orchestration.management.commands.setup_instance.Command._write_env",
        return_value=None,
    )
    @patch(
        "builtins.input",
        side_effect=[
            "2",  # alert source: local
            "1",  # preset: local-monitor (Checkers → Notify)
            "1,2",  # checkers: cpu, memory
            "1",  # notify: slack
            "https://hooks.slack.com/xxx",  # slack webhook
            "ops-alerts",  # channel name
            "Y",  # confirm
        ],
    )
    def test_local_monitor_flow(self, _mock_input, _mock_write_env):
        """Local monitor preset skips alerts stage and has no ingest node."""
        out = StringIO()
        call_command("setup_instance", stdout=out)

        defn = PipelineDefinition.objects.get(created_by="setup_instance")
        assert defn.name == "local-monitor"
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert "ingest" not in node_types
        assert node_types == ["context", "notify"]

        ch = NotificationChannel.objects.get(name="ops-alerts")
        assert ch.driver == "slack"

        output = out.getvalue()
        assert "crontab" in output.lower() or "check_and_alert" in output
