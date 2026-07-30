import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from config.management.commands.setup_cluster import env_upsert, explain_http_error
from config.models import APIKey
from config.security.url_validation import URLNotAllowedError


def _tmp_env(d):
    return Path(d) / ".env"


class EnvUpsertTests(TestCase):
    def test_appends_to_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = _tmp_env(d)
            env_upsert(p, "HUB_URL", "https://h")
            self.assertIn("HUB_URL=https://h", p.read_text())

    def test_replaces_existing_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = _tmp_env(d)
            p.write_text("HUB_URL=old\nOTHER=1\n")
            env_upsert(p, "HUB_URL", "new")
            text = p.read_text()
            self.assertIn("HUB_URL=new", text)
            self.assertNotIn("=old", text)
            self.assertIn("OTHER=1", text)

    def test_appends_when_key_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = _tmp_env(d)
            p.write_text("OTHER=1\n")
            env_upsert(p, "HUB_URL", "h")
            self.assertIn("OTHER=1", p.read_text())
            self.assertIn("HUB_URL=h", p.read_text())

    def test_env_path_points_at_base_dir(self):
        from django.conf import settings

        from config.management.commands.setup_cluster import _env_path

        self.assertEqual(_env_path(), Path(settings.BASE_DIR) / ".env")


class SetupClusterHubTests(TestCase):
    def test_hub_mints_key_and_reports_accepting(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                out = StringIO()
                call_command("setup_cluster", "--role", "hub", "--name", "agent-1", stdout=out)
            env_text = _tmp_env(d).read_text()

        self.assertIn("API_KEY_AUTH_ENABLED=1", env_text)
        self.assertEqual(APIKey.objects.filter(name="agent-1").count(), 1)
        output = out.getvalue()
        self.assertIn("Accepting pushes: yes", output)
        # The raw token is printed once and starts with the stored prefix.
        self.assertIn(APIKey.objects.get(name="agent-1").prefix, output)

    def test_hub_default_name_when_blank(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                with patch("builtins.input", return_value=""):
                    call_command("setup_cluster", "--role", "hub", stdout=StringIO())
        self.assertEqual(APIKey.objects.filter(name="cluster-agent").count(), 1)


class SetupClusterAgentTests(TestCase):
    AGENT_ARGS = [
        "setup_cluster",
        "--role",
        "agent",
        "--hub-url",
        "https://hub.example.com",
        "--instance-id",
        "web-03",
        "--hub-api-key",
        "tok",
    ]

    def _run(self, d, send=None, extra=None):
        out = StringIO()
        with patch("config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)):
            ctx = (
                patch("config.management.commands.setup_cluster.send_to_hub", send)
                if send
                else None
            )
            if ctx:
                with ctx:
                    call_command(*(self.AGENT_ARGS + (extra or [])), stdout=out)
            else:
                call_command(*(self.AGENT_ARGS + (extra or [])), stdout=out)
        return out

    def test_success_writes_env_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            out = self._run(d, send=lambda *a, **k: (202, "{}"))
            env_text = _tmp_env(d).read_text()
        self.assertIn("HUB_URL=https://hub.example.com", env_text)
        self.assertIn("HUB_API_KEY=tok", env_text)
        self.assertIn("INSTANCE_ID=web-03", env_text)
        self.assertIn("Verified", out.getvalue())

    def test_no_verify_skips_push(self):
        def _boom(*a, **k):
            raise AssertionError("send_to_hub must not be called with --no-verify")

        with tempfile.TemporaryDirectory() as d:
            out = self._run(d, send=_boom, extra=["--no-verify"])
        self.assertIn("Skipping verification", out.getvalue())

    def test_401_names_bad_key(self):
        def _send(*a, **k):
            raise HTTPError("http://h", 401, "Unauthorized", None, None)

        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=_send)
        self.assertIn("401", str(ctx.exception))

    def test_403_explains_waf_and_scope(self):
        def _send(*a, **k):
            raise HTTPError("http://h", 403, "Forbidden", None, None)

        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=_send)
        msg = str(ctx.exception)
        self.assertIn("403", msg)
        self.assertIn("allowed_endpoints", msg)

    def test_ssrf_blocked(self):
        def _send(*a, **k):
            raise URLNotAllowedError("private")

        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=_send)
        self.assertIn("SSRF", str(ctx.exception))

    def test_bad_scheme(self):
        def _send(*a, **k):
            raise ValueError("HUB_URL must use http:// or https:// scheme")

        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=_send)
        self.assertIn("scheme", str(ctx.exception))

    def test_connection_error(self):
        def _send(*a, **k):
            raise ConnectionError("refused")

        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=_send)
        self.assertIn("Could not reach", str(ctx.exception))

    def test_non_202_status(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run(d, send=lambda *a, **k: (500, "err"))
        self.assertIn("500", str(ctx.exception))

    def test_requires_url_and_key(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                with patch("builtins.input", return_value=""):
                    with self.assertRaises(CommandError) as ctx:
                        call_command("setup_cluster", "--role", "agent", stdout=StringIO())
        self.assertIn("required", str(ctx.exception))


class SetupClusterInteractiveTests(TestCase):
    def test_prompt_role_hub(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                with patch("builtins.input", side_effect=["1", "keyname"]):
                    call_command("setup_cluster", stdout=StringIO())
        self.assertEqual(APIKey.objects.filter(name="keyname").count(), 1)

    def test_prompt_role_retries_then_agent_with_default_instance(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                with patch(
                    "config.management.commands.setup_cluster.send_to_hub",
                    return_value=(202, "{}"),
                ):
                    # invalid role, then agent; hub_url, blank instance (→ default), key
                    with patch("builtins.input", side_effect=["x", "2", "https://h", "", "tok"]):
                        call_command("setup_cluster", stdout=StringIO())
            env_text = _tmp_env(d).read_text()
        self.assertIn("HUB_URL=https://h", env_text)
        self.assertIn("INSTANCE_ID=", env_text)  # default hostname filled in


class ExplainHttpErrorTests(TestCase):
    def test_generic_code(self):
        self.assertIn("500", explain_http_error(500))
