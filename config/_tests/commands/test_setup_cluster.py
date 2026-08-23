import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.orchestration.routing import resolve_pipeline
from apps.orchestration.testing import clear_lanes
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
                call_command(
                    "setup_cluster", "--role", "hub", "--name", "agent-1", "--no-notify", stdout=out
                )
            env_text = _tmp_env(d).read_text()

        self.assertIn("API_KEY_AUTH_ENABLED=1", env_text)
        self.assertEqual(APIKey.objects.filter(name="agent-1").count(), 1)
        output = out.getvalue()
        self.assertIn("Accepting pushes: yes", output)
        # The raw token is printed once and starts with the stored prefix.
        self.assertIn(APIKey.objects.get(name="agent-1").prefix, output)

    def _run_hub(self, d, *extra):
        out = StringIO()
        with patch("config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)):
            call_command("setup_cluster", "--role", "hub", "--name", "k", *extra, stdout=out)
        return out.getvalue()

    def test_hub_creates_slack_channel(self):
        from apps.notify.models import NotificationChannel

        with tempfile.TemporaryDirectory() as d:
            out = self._run_hub(
                d,
                "--notify-driver",
                "slack",
                "--notify-webhook",
                "https://hooks.slack.com/services/T/B/x",
            )

        ch = NotificationChannel.objects.get(driver="slack")
        self.assertTrue(ch.is_active)
        self.assertEqual(ch.config["webhook_url"], "https://hooks.slack.com/services/T/B/x")
        self.assertIn("slack channel active", out)
        # The channel is wired THROUGH the lane routing actually picks, not a lane
        # this command owns by name. Reading the row by name is what hid the
        # shadowing bug: migration 0012's `catch-all` outranked `default-catch-all`
        # on the id tiebreak, so the named row existed, held the channel, and never
        # ran. Ask the router.
        catchall = resolve_pipeline({"source": "grafana", "severity": "critical"})
        self.assertIsNotNone(catchall)
        self.assertEqual(catchall.match, [])
        self.assertEqual(catchall.routed_channel(), ch)
        # ...and the winning lane actually lists stages: an empty list would attach
        # the channel to a lane that swallows every incident and delivers nothing.
        self.assertIn("notify", catchall.stages)

    def test_hub_creates_generic_channel(self):
        from apps.notify.models import NotificationChannel

        with tempfile.TemporaryDirectory() as d:
            self._run_hub(
                d, "--notify-driver", "generic", "--notify-webhook", "https://ex.example.com/hook"
            )
        self.assertEqual(NotificationChannel.objects.filter(driver="generic").count(), 1)

    def test_hub_invalid_slack_webhook_errors(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(CommandError) as ctx:
            self._run_hub(d, "--notify-driver", "slack", "--notify-webhook", "https://evil.example")
        self.assertIn("Invalid slack", str(ctx.exception))

    def test_hub_no_notify_warns(self):
        from apps.notify.models import NotificationChannel

        with tempfile.TemporaryDirectory() as d:
            out = self._run_hub(d, "--no-notify")
        self.assertIn("Notifications: NONE", out)
        self.assertEqual(NotificationChannel.objects.count(), 0)

    def test_hub_skips_when_channel_already_active(self):
        from apps.notify.models import NotificationChannel

        existing = NotificationChannel.objects.create(
            name="existing", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        with tempfile.TemporaryDirectory() as d:
            out = self._run_hub(d, "--no-notify")
        self.assertIn("routed via the catch-all pipeline", out)
        self.assertEqual(NotificationChannel.objects.count(), 1)
        # The existing channel gets bound to the winning catch-all lane too.
        catchall = resolve_pipeline({"source": "grafana", "severity": "critical"})
        self.assertEqual(catchall.routed_channel(), existing)

    def test_hub_binds_nothing_when_several_channels_are_active(self):
        """With two channels there is no non-arbitrary answer, so it must not pick.

        ``filter(is_active=True).first()`` plus ``Meta.ordering = ["name"]`` picks
        alphabetically — and since binding now covers EVERY delivering lane, that
        accident would land the hub's whole traffic on whichever channel sorts
        first. That is precisely the misroute this branch removes; the operator
        chooses, per lane.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        NotificationChannel.objects.create(
            name="aaa-first", driver="generic", config={"webhook_url": "https://ex.example.com/a"}
        )
        NotificationChannel.objects.create(
            name="zzz-second", driver="slack", config={"webhook_url": "https://hooks.slack.com/z"}
        )
        with tempfile.TemporaryDirectory() as d:
            out = self._run_hub(d, "--no-notify")

        self.assertFalse(PipelineDefinition.objects.filter(channel__isnull=False).exists())
        self.assertIn("Pipeline definitions", out)
        self.assertIn("2 channels active", out)

    def test_hub_leaves_the_lane_shapes_alone_when_several_channels_are_active(self):
        """No binding means no enable_delivery either: the lanes stay as seeded."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        NotificationChannel.objects.create(
            name="aaa-first", driver="generic", config={"webhook_url": "https://ex.example.com/a"}
        )
        NotificationChannel.objects.create(
            name="zzz-second", driver="slack", config={"webhook_url": "https://hooks.slack.com/z"}
        )
        before = sorted(PipelineDefinition.objects.values_list("name", "stages", "is_active"))
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(d, "--no-notify")

        after = sorted(PipelineDefinition.objects.values_list("name", "stages", "is_active"))
        self.assertEqual(before, after)

    def test_hub_keeps_an_already_wired_active_channel(self):
        """A lane has one channel slot; an operator's active choice is not clobbered."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        chosen = NotificationChannel.objects.create(
            name="operator-pick",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/a"},
        )
        PipelineDefinition.objects.create(
            name="default-catch-all",
            match=[],
            stages=["check", "analyze", "notify"],
            channel=chosen,
        )
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(
                d,
                "--notify-driver",
                "slack",
                "--notify-webhook",
                "https://hooks.slack.com/services/T/B/x",
            )
        catchall = PipelineDefinition.objects.get(name="default-catch-all")
        self.assertEqual(catchall.channel, chosen)

    def test_hub_replaces_an_inactive_channel_on_the_catchall(self):
        """A dead channel would falsify the printed "routed via the catch-all" claim."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        dead = NotificationChannel.objects.create(
            name="dead-pick",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/a"},
            is_active=False,
        )
        PipelineDefinition.objects.create(
            name="default-catch-all",
            match=[],
            stages=["check", "analyze", "notify"],
            channel=dead,
        )
        with tempfile.TemporaryDirectory() as d:
            out = self._run_hub(
                d,
                "--notify-driver",
                "slack",
                "--notify-webhook",
                "https://hooks.slack.com/services/T/B/x",
            )
        catchall = PipelineDefinition.objects.get(name="default-catch-all")
        self.assertNotEqual(catchall.channel, dead)
        self.assertTrue(catchall.channel.is_active)
        self.assertIn("routed via the catch-all pipeline", out)

    def test_hub_repairs_broken_catchall_pipeline(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        # The repair path only runs when nothing already routes catch-all traffic,
        # so drop the lanes migration 0012 seeds -- otherwise the seeded `catch-all`
        # wins, and leaving a disabled lane disabled is the correct outcome (see
        # test_broken_named_lane_is_left_alone_when_a_seeded_lane_wins).
        clear_lanes()
        # A pre-existing catch-all that is inactive and not actually catch-all.
        PipelineDefinition.objects.create(
            name="default-catch-all",
            is_active=False,
            match=[{"field": "source", "op": "is", "value": "x"}],
            stages=["check"],
        )
        NotificationChannel.objects.create(
            name="existing", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(d, "--no-notify")
        catchall = PipelineDefinition.objects.get(name="default-catch-all")
        self.assertTrue(catchall.is_active)
        self.assertEqual(catchall.match, [])
        # NOTIFY is guaranteed, and the operator's existing selection is preserved
        # in canonical order rather than overwritten.
        self.assertEqual(catchall.stages, ["check", "notify"])

    def test_a_malformed_stages_column_is_repaired_not_skipped(self):
        """A bare string in ``stages`` must not fool the repair check.

        ``clean()`` only runs on admin forms, so a fixture or shell edit can persist
        ``stages="notify"``. Testing membership against the raw column would substring
        -match (``"notify" in "notify"`` is True) and skip the repair, leaving a lane
        that ``routable_stages()`` normalises to ``[]`` — configured, matched, and
        running nothing. Reading through ``routable_stages()`` makes both the check
        and the repair input honest.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        clear_lanes()
        PipelineDefinition.objects.create(
            name="default-catch-all", is_active=True, match=[], stages="notify"
        )
        # Binding only runs when there is a channel to bind, so the lane needs one
        # for the repair path to be reached at all.
        NotificationChannel.objects.create(
            name="existing", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(d, "--no-notify")
        catchall = PipelineDefinition.objects.get(name="default-catch-all")
        self.assertEqual(catchall.stages, ["notify"])

    def test_the_channel_lands_on_the_lane_that_actually_routes(self):
        """The regression this file could not previously see.

        Every other assertion here reads the lane by name, so all of them passed
        while the bound lane never ran: 0012's ``catch-all`` and this command's
        ``default-catch-all`` both sit at priority 1000, the tie breaks on ``id``,
        and ``migrate`` runs before ``setup_cluster`` — so the seed always won and
        delivery fell through to "first active channel by name". Two channels
        exist here precisely so that fallback cannot mask the bug: the wrong one
        sorts first alphabetically.
        """
        from apps.notify.models import NotificationChannel

        decoy = NotificationChannel.objects.create(
            name="aaa-decoy", driver="generic", config={"webhook_url": "https://ex.example.com/d"}
        )
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(
                d,
                "--notify-driver",
                "slack",
                "--notify-webhook",
                "https://hooks.slack.com/services/T/B/x",
            )
        # _ensure_notification_channel adopts the existing active channel, so the
        # decoy IS the channel under test -- and it must be reachable by routing.
        # Deliberately NOT source=cluster: that is claimed by the seeded
        # cluster-nodes lane, which carries no channel by design.
        lane = resolve_pipeline({"source": "grafana", "severity": "critical", "labels": {}})
        self.assertIsNotNone(lane)
        self.assertEqual(lane.routed_channel(), decoy)

    def test_broken_named_lane_is_left_alone_when_a_seeded_lane_wins(self):
        """No resurrection of a lane an operator disabled.

        With the seeded catch-all present there is nothing to repair: it already
        routes. Reactivating ``default-catch-all`` would silently re-enable a lane
        the operator turned off.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        disabled = PipelineDefinition.objects.create(
            name="default-catch-all", is_active=False, match=[], stages=["notify"]
        )
        NotificationChannel.objects.create(
            name="existing", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        with tempfile.TemporaryDirectory() as d:
            self._run_hub(d, "--no-notify")
        disabled.refresh_from_db()
        self.assertFalse(disabled.is_active)
        self.assertIsNone(disabled.channel)
        # The seeded lane took the channel instead.
        self.assertEqual(resolve_pipeline({"source": "grafana"}).name, "catch-all")

    def test_hub_interactive_slack_channel(self):
        from apps.notify.models import NotificationChannel

        with tempfile.TemporaryDirectory() as d:
            with patch(
                "config.management.commands.setup_cluster._env_path", return_value=_tmp_env(d)
            ):
                with patch(
                    "builtins.input",
                    side_effect=["slack", "https://hooks.slack.com/services/T/B/x"],
                ):
                    call_command("setup_cluster", "--role", "hub", "--name", "k", stdout=StringIO())
        self.assertEqual(NotificationChannel.objects.filter(driver="slack").count(), 1)

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
                # role=1(hub), name, notify prompt=skip
                with patch("builtins.input", side_effect=["1", "keyname", "skip"]):
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
