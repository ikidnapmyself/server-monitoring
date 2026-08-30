"""
Management command to replay an alert payload through the pipeline, synchronously.

``run_pipeline`` is a producer like every other: it writes alerts, lets incidents
form, and hands the materially changed ones to ``apps.orchestration.intake``. It
differs from the webhook only in draining its own runs before returning, because
a CLI caller expects one command to finish the work.

Usage:
    # Replay a sample alert payload
    python manage.py run_pipeline --sample

    # Replay a custom JSON payload
    python manage.py run_pipeline --payload '{"alerts": [...]}'

    # Replay a payload from a file
    python manage.py run_pipeline --file alert.json

    # Replay it as a specific driver's payload
    python manage.py run_pipeline --sample --source grafana

    # Run this machine's checkers (DEPRECATED — use `manage.py check_health`)
    python manage.py run_pipeline --checks-only

    # Dry run (show what would happen)
    python manage.py run_pipeline --sample --dry-run
"""

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.orchestration.models import PipelineOrigin
from config.security import PathNotAllowedError, resolve_safe_path

# Kept, not deleted: an operator SSHes into a node and runs this by hand, and a
# removed command is a worse surprise than a warning. ``check_health`` is the
# synchronous local entrypoint now and does this same work through the same
# path — CheckAlertBridge, then the shared intake.
CHECKS_ONLY_DEPRECATION = (
    "--checks-only is deprecated: use `manage.py check_health` instead. "
    "It runs the same checkers, writes the same alerts and drains the same runs; "
    "--warning-threshold, --critical-threshold and --no-notify carry over."
)


class Command(BaseCommand):
    help = "Replay an alert payload through the pipeline: ingest, then run the matched lane"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sample",
            action="store_true",
            help="Use a sample alert payload for testing",
        )
        parser.add_argument(
            "--payload",
            type=str,
            help="JSON payload string",
        )
        parser.add_argument(
            "--file",
            type=str,
            help="Path to JSON file containing payload",
        )
        parser.add_argument(
            "--source",
            type=str,
            default="cli",
            help="Source system, also the driver name (default: cli, meaning auto-detect)",
        )
        parser.add_argument(
            "--environment",
            type=str,
            default="development",
            help="Environment name (default: development)",
        )
        parser.add_argument(
            "--trace-id",
            type=str,
            help="Custom trace ID for correlation",
        )
        parser.add_argument(
            "--checks-only",
            action="store_true",
            help="DEPRECATED (use `check_health`): run this machine's checkers instead",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without executing",
        )
        parser.add_argument(
            "--notify-driver",
            type=str,
            default="generic",
            help="Notification driver to use (default: generic)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output result as JSON",
        )
        parser.add_argument(
            "--checkers",
            nargs="+",
            help="Specific checkers to run (e.g., cpu memory disk). Only used with --checks-only.",
        )
        parser.add_argument(
            "--hostname",
            type=str,
            help="Override hostname in alert labels.",
        )
        parser.add_argument(
            "--label",
            action="append",
            dest="labels",
            metavar="KEY=VALUE",
            help="Additional label for alerts (can be repeated).",
        )
        parser.add_argument(
            "--no-incidents",
            action="store_true",
            help=(
                "Skip automatic incident creation. With --checks-only this also "
                "makes the run purely diagnostic: no incident forms, so nothing is "
                "enqueued and nothing is analyzed or notified. Alerts are still "
                "recorded."
            ),
        )
        parser.add_argument(
            "--no-notify",
            action="store_true",
            help=(
                "Run the matched lane without its NOTIFY stage. For looking at a "
                "machine in real time (SSH in, run the checks, read the analysis) "
                "without telling anyone. The lane itself is unchanged; only this "
                "run is scoped."
            ),
        )
        parser.add_argument(
            "--warning-threshold",
            type=float,
            help="Override warning threshold for all checkers.",
        )
        parser.add_argument(
            "--critical-threshold",
            type=float,
            help="Override critical threshold for all checkers.",
        )

    def handle(self, *args, **options):
        checks_only = bool(options.get("checks_only"))
        if checks_only:
            # stderr, so --json stdout stays parseable for whatever wraps this.
            self.stderr.write(self.style.WARNING(CHECKS_ONLY_DEPRECATION))

        # Input is validated before --dry-run is honoured: a dry run that accepted
        # a payload the real run would reject is worse than useless.
        inner_payload = {} if checks_only else self._get_inner_payload(options)
        labels = self._parse_labels(options.get("labels"))
        checker_names = options.get("checkers")
        checker_configs = self._build_checker_configs(options, checker_names) if checks_only else {}

        if options["dry_run"]:
            self._show_dry_run(inner_payload, options, checks_only)
            return

        trace_id = options.get("trace_id") or str(uuid.uuid4())
        self._preamble(options)

        try:
            if checks_only:
                summary = self._run_checks(
                    trace_id, options, checker_names, checker_configs, labels
                )
            else:
                summary = self._replay_payload(trace_id, options, inner_payload)
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"Pipeline failed: {e}")

        self._report(summary, options)

    # ------------------------------------------------------------------ producers

    def _replay_payload(self, trace_id: str, options: dict, inner_payload: dict) -> dict:
        """Ingest the payload here, then drain the runs its incidents earned.

        Exactly what the webhook does — ``process_webhook`` then ``enqueue_for`` —
        with ``sync=True``, because the operator who typed the command is the only
        one who is going to drain anything.
        """
        from apps.alerts.services import AlertOrchestrator
        from apps.orchestration.intake import enqueue_for

        # "cli" is the default and means "no driver named": let the payload be
        # sniffed, as an unlabelled webhook is.
        driver = options["source"] if options["source"] != "cli" else None

        result = AlertOrchestrator(trace_id=trace_id).process_webhook(inner_payload, driver=driver)

        if not result.driver_resolved:
            # Nothing understood the payload, so nothing was written. The operator
            # is the only one who can fix that, and a silent success would hide it.
            raise CommandError(
                "Could not detect a driver for the payload: " + "; ".join(result.errors)
            )

        runs = enqueue_for(
            result,
            trace_id=trace_id,
            origin=PipelineOrigin.MANUAL,
            source=options["source"],
            environment=options["environment"],
            no_notify=bool(options.get("no_notify")),
            sync=True,
        )
        return {
            "trace_id": trace_id,
            "incidents": [run.incident_id for run in runs],
            "alerts": len(result.alerts),
            "errors": list(result.errors),
        }

    def _run_checks(
        self,
        trace_id: str,
        options: dict,
        checker_names: list | None,
        checker_configs: dict,
        labels: dict,
    ) -> dict:
        """Run the named checkers here and drain the runs their incidents earned.

        The deprecated ``--checks-only``, expressed as the same two steps every
        producer takes. ``--no-incidents`` needs no special case any more: it makes
        the bridge create no incidents, so no alert has one, so
        ``material_incident_ids`` is empty and ``enqueue_for`` enqueues nothing.
        Nothing is routed, analyzed or notified — the old "just check, do not
        disturb anything" contract, arrived at rather than coded for.
        """
        from apps.alerts.check_integration import CheckAlertBridge
        from apps.orchestration.intake import enqueue_for

        hostname = options.get("hostname")
        bridge_kwargs: dict = {
            "trace_id": trace_id,
            "auto_create_incidents": not options.get("no_incidents", False),
            # A --hostname means this diagnosis is ABOUT another machine: the
            # checkers run here but the alerts carry that machine's name, so this
            # run must not claim that identity for the local Node registry. Same
            # predicate that decides whether the bridge gets a hostname at all.
            "register_node": not hostname,
        }
        if hostname:
            bridge_kwargs["hostname"] = hostname

        result = CheckAlertBridge(**bridge_kwargs).run_checks_and_alert(
            checker_names=checker_names,
            checker_configs=checker_configs or None,
            labels=labels or None,
        )

        runs = enqueue_for(
            result,
            trace_id=trace_id,
            origin=PipelineOrigin.CHECKER_GENERATED,
            # The bridge writes every checker alert under this source, whoever ran
            # it; recording the run under anything else would make the run and its
            # alerts disagree about where the work came from.
            source=CheckAlertBridge.SOURCE_NAME,
            environment=options["environment"],
            no_notify=bool(options.get("no_notify")),
            sync=True,
        )
        return {
            "trace_id": trace_id,
            "incidents": [run.incident_id for run in runs],
            "checks": result.checks_run,
            "alerts": len(result.alerts),
            "errors": list(result.errors),
        }

    # -------------------------------------------------------------------- options

    def _get_inner_payload(self, options) -> dict:
        """Read the payload to replay from --payload / --file / --sample."""
        if options["payload"]:
            try:
                return json.loads(options["payload"])
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON payload: {e}")
        if options["file"]:
            try:
                file_path = resolve_safe_path(options["file"])
            except PathNotAllowedError as e:
                raise CommandError(str(e))
            try:
                with open(file_path) as f:
                    return json.load(f)
            except FileNotFoundError:
                raise CommandError(f"File not found: {options['file']}")
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON in file: {e}")
        if options["sample"]:
            return self._get_sample_payload(options["source"])
        raise CommandError("Must specify --sample, --payload, --file, or --checks-only")

    def _parse_labels(self, raw_labels) -> dict:
        labels: dict[str, str] = {}
        for label in raw_labels or []:
            if "=" not in label:
                raise CommandError(f"Invalid label format: {label}. Use KEY=VALUE.")
            key, value = label.split("=", 1)
            labels[key] = value
        return labels

    def _build_checker_configs(self, options, checker_names) -> dict:
        """Threshold overrides, per checker that will actually run.

        With --checkers, only those; without, every registered checker — the same
        expansion the old ``__all__`` payload key stood for, done here where the
        names are known.
        """
        base: dict[str, float] = {}
        if options.get("warning_threshold") is not None:
            base["warning_threshold"] = options["warning_threshold"]
        if options.get("critical_threshold") is not None:
            base["critical_threshold"] = options["critical_threshold"]
        if not base:
            return {}

        from apps.checkers.checkers import CHECKER_REGISTRY

        names = checker_names if checker_names is not None else list(CHECKER_REGISTRY.keys())
        return {name: dict(base) for name in names}

    def _get_sample_payload(self, source: str) -> dict[str, object]:
        """Generate a sample payload for testing."""
        samples: dict[str, dict[str, object]] = {
            "alertmanager": {
                "version": "4",
                "receiver": "webhook",
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighCPUUsage",
                            "severity": "warning",
                            "instance": "localhost:9090",
                            "job": "node",
                        },
                        "annotations": {
                            "summary": "High CPU usage detected",
                            "description": "CPU usage is above 80% for 5 minutes",
                        },
                        "startsAt": "2024-01-10T10:00:00Z",
                        "fingerprint": "sample-cpu-alert-001",
                    }
                ],
            },
            "grafana": {
                "receiver": "webhook",
                "status": "firing",
                "state": "alerting",
                "title": "Test Alert from Grafana",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighMemoryUsage",
                            "severity": "critical",
                        },
                        "annotations": {
                            "summary": "Memory usage critical",
                        },
                        "startsAt": "2024-01-10T10:00:00Z",
                    }
                ],
            },
            "generic": {
                "name": "Test Alert",
                "status": "firing",
                "severity": "warning",
                "description": "This is a test alert from the CLI",
                "labels": {
                    "source": "cli-test",
                    "environment": "development",
                },
            },
        }

        # Default to generic if source not found
        return samples.get(source, samples["generic"])

    # --------------------------------------------------------------------- output

    def _preamble(self, options) -> None:
        """Announce the run — on stderr under --json, so stdout stays parseable."""
        stream = self.stderr if options["json"] else self.stdout
        stream.write("Starting pipeline...")
        stream.write(f"  Source: {options['source']}")
        stream.write(f"  Environment: {options['environment']}")
        stream.write("")

    def _show_dry_run(self, inner_payload: dict, options: dict, checks_only: bool):
        """Display what would happen in a dry run."""
        self.stdout.write(self.style.WARNING("=== DRY RUN ==="))
        self.stdout.write("")
        self.stdout.write("Pipeline Configuration:")
        self.stdout.write(f"  Source: {options['source']}")
        self.stdout.write(f"  Environment: {options['environment']}")
        self.stdout.write(f"  Notify Driver: {options['notify_driver']}")
        self.stdout.write("")
        if checks_only:
            names = options.get("checkers")
            self.stdout.write(f"  Checkers: {', '.join(names) if names else 'all'}")
        else:
            self.stdout.write("Payload:")
            self.stdout.write(json.dumps(inner_payload, indent=2))
        self.stdout.write("")
        self.stdout.write("Pipeline Stages:")
        if checks_only:
            self.stdout.write("  1. CHECK   - Run this machine's checkers, record alerts")
        else:
            self.stdout.write("  1. INGEST  - Parse alert payload, create/update Alert + Incident")
        self.stdout.write("  2. ENQUEUE - One run per materially changed incident")
        self.stdout.write("  3. DRAIN   - Run each queued incident through its matched lane")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Use without --dry-run to execute"))

    def _report(self, summary: dict, options: dict) -> None:
        """Report what was written and what ran. Errors always reach stderr."""
        for error in summary["errors"]:
            self.stderr.write(self.style.WARNING(f"Error: {error}"))

        if options["json"]:
            self.stdout.write(json.dumps(summary, indent=2, default=str))
            return

        self.stdout.write(
            f"Recorded {summary['alerts']} alert(s), "
            f"ran {len(summary['incidents'])} incident run(s) "
            f"(trace_id={summary['trace_id']})"
        )
