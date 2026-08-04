from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.intelligence.models import IntelligenceProvider


class SetupIntelligenceTests(TestCase):
    def test_configures_ai_provider_non_interactive(self):
        out = StringIO()
        call_command(
            "setup_intelligence",
            "--provider",
            "openai",
            "--api-key",
            "sk-test",
            "--model",
            "gpt-4o",
            stdout=out,
        )
        p = IntelligenceProvider.objects.get(name="setup-openai")
        self.assertEqual(p.provider, "openai")
        self.assertTrue(p.is_active)
        self.assertEqual(p.config, {"api_key": "sk-test", "model": "gpt-4o"})
        self.assertIn("active", out.getvalue())

    def test_ai_provider_defaults_model(self):
        call_command("setup_intelligence", "--provider", "openai", "--api-key", "sk-x")
        p = IntelligenceProvider.objects.get(name="setup-openai")
        self.assertEqual(p.config["model"], "gpt-4o-mini")  # openai default_model

    def test_local_needs_no_key(self):
        call_command("setup_intelligence", "--provider", "local")
        p = IntelligenceProvider.objects.get(name="setup-local")
        self.assertTrue(p.is_active)
        self.assertEqual(p.config, {})

    def test_unknown_provider_errors(self):
        with self.assertRaises(CommandError):
            call_command("setup_intelligence", "--provider", "nope")

    def test_missing_api_key_errors(self):
        with patch("builtins.input", return_value=""):
            with self.assertRaises(CommandError):
                call_command("setup_intelligence", "--provider", "openai")

    def test_interactive_selection(self):
        # role prompt returns the index of "local" in PROVIDERS
        from apps.intelligence.providers import PROVIDERS

        local_idx = str(list(PROVIDERS).index("local") + 1)
        with patch("builtins.input", side_effect=["bad", local_idx]):
            call_command("setup_intelligence", stdout=StringIO())
        self.assertTrue(IntelligenceProvider.objects.get(name="setup-local").is_active)

    def test_interactive_ai_prompts_key_and_model(self):
        # No --api-key → interactive: prompt for key, then model.
        with patch("builtins.input", side_effect=["sk-interactive", "gpt-4o"]):
            call_command("setup_intelligence", "--provider", "openai", stdout=StringIO())
        p = IntelligenceProvider.objects.get(name="setup-openai")
        self.assertEqual(p.config, {"api_key": "sk-interactive", "model": "gpt-4o"})

    def test_interactive_ai_blank_model_uses_default(self):
        with patch("builtins.input", side_effect=["sk-x", ""]):
            call_command("setup_intelligence", "--provider", "openai", stdout=StringIO())
        p = IntelligenceProvider.objects.get(name="setup-openai")
        self.assertEqual(p.config["model"], "gpt-4o-mini")

    def test_single_active_enforced(self):
        IntelligenceProvider.objects.create(
            name="old", provider="openai", config={"api_key": "k"}, is_active=True
        )
        call_command("setup_intelligence", "--provider", "local")
        active = IntelligenceProvider.objects.filter(is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.first().name, "setup-local")
