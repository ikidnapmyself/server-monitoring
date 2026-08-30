"""
Integration module for creating alerts from checker results.

This module provides utilities to convert CheckResult from the checkers app
into Alert objects in the alerts app.

Usage:
    from apps.checkers.checkers import CPUChecker
    from apps.alerts.check_integration import CheckAlertBridge

    # Run a check and create an alert
    checker = CPUChecker()
    result = checker.check()

    bridge = CheckAlertBridge()
    alert_result = bridge.process_check_result(result)

    # Or run multiple checks
    from apps.checkers.checkers import CHECKER_REGISTRY
    results = bridge.run_checks_and_alert(["cpu", "memory", "disk"])
"""

import logging
import socket
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.alerts.drivers.base import ParsedAlert, ParsedPayload
from apps.alerts.identity import checker_fingerprint, local_instance_id
from apps.alerts.models import (
    Alert,
    AlertSeverity,
    Node,
)
from apps.alerts.services import AlertOrchestrator, ProcessingResult
from apps.checkers.checkers import (
    CHECKER_REGISTRY,
    CheckResult,
    CheckStatus,
)

logger = logging.getLogger(__name__)


# Mapping from CheckStatus to AlertSeverity
STATUS_TO_SEVERITY = {
    CheckStatus.CRITICAL: AlertSeverity.CRITICAL,
    CheckStatus.WARNING: AlertSeverity.WARNING,
    CheckStatus.OK: AlertSeverity.INFO,
    CheckStatus.UNKNOWN: AlertSeverity.WARNING,
}

# Mapping from CheckStatus to alert status (firing/resolved)
STATUS_TO_ALERT_STATUS = {
    CheckStatus.CRITICAL: "firing",
    CheckStatus.WARNING: "firing",
    CheckStatus.OK: "resolved",
    CheckStatus.UNKNOWN: "firing",
}


@dataclass
class CheckAlertResult:
    """Result of processing check results into alerts."""

    alerts_created: int = 0
    alerts_updated: int = 0
    alerts_resolved: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    checks_run: int = 0
    errors: list[str] = field(default_factory=list)
    # Alert rows these checks created, updated or resolved, in checker order.
    # Bounded to one run; see ProcessingResult.alerts for the consumer caveats.
    alerts: list[Alert] = field(default_factory=list)
    # The subset of `alerts` whose write was material — see apps.alerts.materiality.
    material_alerts: list[Alert] = field(default_factory=list)
    # The raw CheckResult objects, in checker order, for the audit trail.
    check_results: list[CheckResult] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def merge(self, other: "CheckAlertResult") -> None:
        """Fold another batch's counts and rows into this one, in place."""
        self.alerts_created += other.alerts_created
        self.alerts_updated += other.alerts_updated
        self.alerts_resolved += other.alerts_resolved
        self.incidents_created += other.incidents_created
        self.incidents_updated += other.incidents_updated
        self.checks_run += other.checks_run
        self.alerts.extend(other.alerts)
        self.material_alerts.extend(other.material_alerts)
        self.check_results.extend(other.check_results)
        self.errors.extend(other.errors)


class CheckAlertBridge:
    """
    Bridge between checkers and alerts.

    Converts CheckResult objects from the checkers app into alerts.
    Supports both one-off check-to-alert conversions and batch processing.
    """

    # Alert identity is the pair ``(fingerprint, source)`` — see the lookup at
    # apps/alerts/services.py:269. A checker alert written here and the same
    # machine's pushed result must therefore share both halves, or one condition
    # on one machine becomes two Alert rows.
    SOURCE_NAME = "cluster"

    def __init__(
        self,
        auto_create_incidents: bool = True,
        auto_resolve_incidents: bool = True,
        hostname: str | None = None,
        instance_id: str | None = None,
        trace_id: str = "",
        register_node: bool = True,
    ):
        """
        Initialize the bridge.

        Args:
            auto_create_incidents: Automatically create incidents for critical alerts.
            auto_resolve_incidents: Automatically resolve incidents when alerts resolve.
            hostname: Override hostname for alert labels. Defaults to system hostname.
            instance_id: Override this machine's registry key, which keys the alert
                fingerprint and links the alert to its Node. Defaults to
                ``local_instance_id()``.
            trace_id: Correlation ID stamped on alerts + CheckRuns from this run.
            register_node: Register this machine in the Node registry before writing
                alerts. Decline it when the bridge is not describing the machine it
                runs on: hub-side diagnosis of another machine's incident runs the
                checkers here but labels the alerts with that incident's hostname, so
                that caller must not claim that hostname for this machine's registry
                row. A caller that names no other machine is describing this one and
                should leave this on.
        """
        self.orchestrator = AlertOrchestrator(
            auto_create_incidents=auto_create_incidents,
            auto_resolve_incidents=auto_resolve_incidents,
            trace_id=trace_id,
            # Checkers report every checker every tick; a healthy one has nothing
            # to record until it first fires.
            create_from_resolved=False,
        )
        self.hostname = hostname or socket.gethostname()
        self.instance_id = instance_id or local_instance_id()
        self.trace_id = trace_id
        self.register_node = register_node

    def check_result_to_parsed_alert(
        self,
        result: CheckResult,
        labels: dict[str, str] | None = None,
    ) -> ParsedAlert:
        """
        Convert a CheckResult to a ParsedAlert.

        Args:
            result: The CheckResult from a checker.
            labels: Additional labels to attach to the alert.

        Returns:
            ParsedAlert ready for processing.
        """
        # Build labels
        alert_labels = {
            "hostname": self.hostname,
            "checker": result.checker_name,
        }
        if labels:
            alert_labels.update(labels)
        # Set AFTER the caller's labels, deliberately. The fingerprint below and
        # this label are one fact — which machine this result is about — read by
        # two different consumers (dedup, and resolve_node's alert→Node link). A
        # caller must not be able to split them: ``run_pipeline --label
        # instance_id=web-03`` would otherwise fingerprint to the hub and link to
        # web-03. To change the identity, change ``instance_id`` on the bridge.
        alert_labels["instance_id"] = self.instance_id

        # Add metrics as labels (for deduplication and grouping)
        for key, value in result.metrics.items():
            if isinstance(value, (str, int, float, bool)):
                alert_labels[f"metric_{key}"] = str(value)

        # Fingerprint is keyed on the instance id, so the same checker on the same
        # machine dedups whether it was run locally or pushed from a node.
        fingerprint = checker_fingerprint(self.instance_id, result.checker_name)

        # Determine severity and status
        severity = STATUS_TO_SEVERITY.get(result.status, AlertSeverity.WARNING)
        alert_status = STATUS_TO_ALERT_STATUS.get(result.status, "firing")

        # Build description
        description = result.message
        if result.error:
            description = f"{description}\nError: {result.error}"

        # Build annotations from metrics
        annotations = {}
        for key, value in result.metrics.items():
            annotations[key] = str(value)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=f"{result.checker_name.upper()} Check Alert",
            status=alert_status,
            severity=severity,
            description=description,
            labels=alert_labels,
            annotations=annotations,
            started_at=timezone.now(),
            ended_at=timezone.now() if alert_status == "resolved" else None,
            raw_payload={
                "checker_name": result.checker_name,
                "status": result.status.value,
                "message": result.message,
                "metrics": result.metrics,
                "error": result.error,
            },
        )

    def process_check_result(
        self,
        result: CheckResult,
        labels: dict[str, str] | None = None,
    ) -> ProcessingResult:
        """
        Process a single CheckResult and create/update alerts.

        Args:
            result: The CheckResult from a checker.
            labels: Additional labels to attach to the alert.

        Returns:
            ProcessingResult with counts of created/updated records.
        """
        parsed_alert = self.check_result_to_parsed_alert(result, labels)
        parsed_payload = ParsedPayload(
            alerts=[parsed_alert],
            source=self.SOURCE_NAME,
        )

        # Use the orchestrator's internal processing by constructing a payload
        return self._process_parsed_payload(parsed_payload)

    def _process_parsed_payload(self, parsed: ParsedPayload) -> ProcessingResult:
        """Process a parsed payload through the orchestrator."""
        result = ProcessingResult()

        try:
            with transaction.atomic():
                if self.register_node:
                    # The machine we just checked belongs in the registry the moment
                    # it produces truth about itself. Same row a cluster push would
                    # create if this machine pushed to a hub, so the hub is a node
                    # like any other. Must precede alert creation: _create_alert
                    # resolves the node from the instance_id label and only links to
                    # an already-registered row.
                    Node.upsert(
                        instance_id=self.instance_id,
                        hostname=self.hostname,
                        source="local",
                    )
                for parsed_alert in parsed.alerts:
                    self.orchestrator._process_alert(parsed_alert, parsed.source, result)
        except Exception as e:
            # The bridge reports its failures rather than raising them: a health
            # check must still print its results. But the batch rolled back, so
            # none of the rows it appended exist — see
            # ``ProcessingResult.discard_writes`` for why reporting them would be
            # worse than reporting nothing.
            logger.exception("Error processing check result")
            result.discard_writes()
            result.errors.append(str(e))
            return result

        try:
            # Handle incident auto-resolution. Past the commit: a failure here
            # takes nothing back, so the writes above still stand.
            if self.orchestrator.auto_resolve_incidents:
                self.orchestrator._check_incident_resolution()
        except Exception as e:
            logger.exception("Error processing check result")
            result.errors.append(str(e))

        return result

    def run_check_and_alert(
        self,
        checker_name: str,
        checker_kwargs: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
    ) -> tuple[CheckResult, ProcessingResult]:
        """
        Run a single check and create an alert from the result.

        Args:
            checker_name: Name of the checker to run (from CHECKER_REGISTRY).
            checker_kwargs: Optional kwargs to pass to the checker.
            labels: Additional labels for the alert.

        Returns:
            Tuple of (CheckResult, ProcessingResult).

        Raises:
            ValueError: If checker_name is not in the registry or is disabled.
        """
        check_result = self._run_checker(checker_name, checker_kwargs)
        processing_result = self.process_check_result(check_result, labels)

        return check_result, processing_result

    def _run_checker(
        self,
        checker_name: str,
        checker_kwargs: dict[str, Any] | None = None,
    ) -> CheckResult:
        """Run one registered checker and return its result. No alert is written.

        Raises:
            ValueError: If checker_name is not in the registry.
        """
        if checker_name not in CHECKER_REGISTRY:
            raise ValueError(
                f"Unknown checker: {checker_name}. "
                f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
            )

        checker_class = CHECKER_REGISTRY[checker_name]
        checker = checker_class(**(checker_kwargs or {}))
        return checker.run(trace_id=self.trace_id)

    def process_check_results(
        self,
        results: list[CheckResult],
        labels: dict[str, str] | None = None,
    ) -> CheckAlertResult:
        """Record alerts for results this caller already ran.

        ``run_checks_and_alert`` runs the checkers itself; this is the same
        recording step for a caller that has its own results in hand.

        Args:
            results: CheckResults to record.
            labels: Additional labels for all alerts.

        Returns:
            CheckAlertResult with aggregate counts.
        """
        aggregate = CheckAlertResult()

        for check_result in results:
            processing_result = self.process_check_result(check_result, labels)

            aggregate.checks_run += 1
            aggregate.alerts_created += processing_result.alerts_created
            aggregate.alerts_updated += processing_result.alerts_updated
            aggregate.alerts_resolved += processing_result.alerts_resolved
            aggregate.incidents_created += processing_result.incidents_created
            aggregate.incidents_updated += processing_result.incidents_updated
            aggregate.alerts.extend(processing_result.alerts)
            aggregate.material_alerts.extend(processing_result.material_alerts)
            aggregate.check_results.append(check_result)

            if processing_result.has_errors:
                aggregate.errors.extend(processing_result.errors)

        return aggregate

    def run_checks_and_alert(
        self,
        checker_names: list[str] | None = None,
        checker_configs: dict[str, dict[str, Any]] | None = None,
        labels: dict[str, str] | None = None,
    ) -> CheckAlertResult:
        """
        Run multiple checks and create alerts from the results.

        Args:
            checker_names: List of checker names to run. If None, runs all.
            checker_configs: Dict mapping checker names to their kwargs.
            labels: Additional labels for all alerts.

        Returns:
            CheckAlertResult with aggregate counts.
        """
        checker_configs = checker_configs or {}

        if checker_names is None:
            checker_names = list(CHECKER_REGISTRY.keys())

        result = CheckAlertResult()
        for checker_name in checker_names:
            try:
                check_result = self._run_checker(
                    checker_name, checker_configs.get(checker_name, {})
                )
            except Exception as e:
                logger.exception(f"Error running checker {checker_name}")
                result.errors.append(f"{checker_name}: {str(e)}")
                continue

            # Record as we go rather than batching at the end: each alert is
            # timestamped at its own checker's completion, and a later checker
            # that hangs or dies leaves the earlier ones already recorded.
            # Recording is the same step a caller with its own results takes.
            result.merge(self.process_check_results([check_result], labels))

        return result
