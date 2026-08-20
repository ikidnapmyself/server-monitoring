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

import hashlib
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.alerts.context_keys import context_key_for
from apps.alerts.drivers.base import ParsedAlert, ParsedPayload
from apps.alerts.materiality import is_material_change
from apps.alerts.models import (
    Alert,
    AlertHistory,
    AlertSeverity,
    AlertStatus,
    Incident,
    IncidentStatus,
)
from apps.alerts.services import AlertOrchestrator, ProcessingResult, resolve_node
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

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class CheckAlertBridge:
    """
    Bridge between checkers and alerts.

    Converts CheckResult objects from the checkers app into alerts.
    Supports both one-off check-to-alert conversions and batch processing.
    """

    SOURCE_NAME = "server-checkers"

    def __init__(
        self,
        auto_create_incidents: bool = True,
        auto_resolve_incidents: bool = True,
        hostname: str | None = None,
        trace_id: str = "",
    ):
        """
        Initialize the bridge.

        Args:
            auto_create_incidents: Automatically create incidents for critical alerts.
            auto_resolve_incidents: Automatically resolve incidents when alerts resolve.
            hostname: Override hostname for alert labels. Defaults to system hostname.
            trace_id: Correlation ID stamped on alerts + CheckRuns from this run.
        """
        self.orchestrator = AlertOrchestrator(
            auto_create_incidents=auto_create_incidents,
            auto_resolve_incidents=auto_resolve_incidents,
            trace_id=trace_id,
        )
        self.hostname = hostname or socket.gethostname()
        self.trace_id = trace_id

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

        # Add metrics as labels (for deduplication and grouping)
        for key, value in result.metrics.items():
            if isinstance(value, (str, int, float, bool)):
                alert_labels[f"metric_{key}"] = str(value)

        # Generate fingerprint based on checker name and hostname
        fingerprint = self._generate_fingerprint(result.checker_name, self.hostname)

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

    def _generate_fingerprint(self, checker_name: str, hostname: str) -> str:
        """Generate a stable fingerprint for deduplication."""
        fingerprint_str = f"{checker_name}:{hostname}"
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]

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
                for parsed_alert in parsed.alerts:
                    self._process_alert(parsed_alert, parsed.source, result)

            # Handle incident auto-resolution
            if self.orchestrator.auto_resolve_incidents:
                self._check_incident_resolution()

        except Exception as e:
            logger.exception("Error processing check result")
            result.errors.append(str(e))

        return result

    def _process_alert(
        self,
        parsed: ParsedAlert,
        source: str,
        result: ProcessingResult,
    ) -> Alert | None:
        """Process a single parsed alert - create, update, or resolve."""
        # Look for existing alert with same fingerprint
        existing_alert = Alert.objects.filter(
            fingerprint=parsed.fingerprint,
            source=source,
        ).first()

        if existing_alert:
            if parsed.status == "resolved" and existing_alert.status == AlertStatus.FIRING:
                return self._resolve_alert(existing_alert, parsed, result)
            elif parsed.status == "firing":
                return self._update_alert(existing_alert, parsed, result)
            else:
                # Already resolved, no change needed
                return existing_alert
        else:
            if parsed.status == "firing":
                return self._create_alert(parsed, source, result)
            else:
                # Resolved alert for something we don't have - skip
                return None

    def _create_alert(
        self,
        parsed: ParsedAlert,
        source: str,
        result: ProcessingResult,
    ) -> Alert:
        """Create a new alert from parsed data.

        trace_id/node are stamped at creation only (origin semantics); a later
        refire updates the alert via _update_alert but keeps its original stamps.
        """
        alert = Alert.objects.create(
            fingerprint=parsed.fingerprint,
            source=source,
            name=parsed.name,
            severity=parsed.severity,
            status=AlertStatus.FIRING,
            description=parsed.description,
            labels=parsed.labels,
            annotations=parsed.annotations,
            raw_payload=parsed.raw_payload,
            started_at=parsed.started_at,
            trace_id=self.trace_id,
            node=resolve_node(parsed.labels),
            context_key=context_key_for(
                (parsed.labels or {}).get("checker", ""), parsed.annotations
            ),
        )

        AlertHistory.objects.create(
            alert=alert,
            event="created",
            new_status=AlertStatus.FIRING,
            details={"source": source, "checker": parsed.labels.get("checker", "")},
        )

        result.alerts_created += 1
        logger.info(f"Created alert from check: {alert.name} ({alert.fingerprint})")

        # Auto-create incident if enabled and this is critical/warning. Delegate to
        # the orchestrator's unified (name, instance)-scoped grouping — no separate
        # checker grouping path.
        if self.orchestrator.auto_create_incidents and parsed.severity in (
            AlertSeverity.CRITICAL,
            AlertSeverity.WARNING,
        ):
            self.orchestrator._create_or_attach_incident(alert, result)

        result.alerts.append(alert)
        # A brand-new alert is always material: there is no prior state to compare.
        result.material_alerts.append(alert)
        return alert

    def _update_alert(
        self,
        alert: Alert,
        parsed: ParsedAlert,
        result: ProcessingResult,
    ) -> Alert:
        """Update an existing alert with new data."""
        old_severity = alert.severity
        old_status = alert.status
        old_key = alert.context_key
        new_key = context_key_for((parsed.labels or {}).get("checker", ""), parsed.annotations)

        alert.severity = parsed.severity
        alert.description = parsed.description
        alert.annotations = parsed.annotations
        alert.raw_payload = parsed.raw_payload
        alert.context_key = new_key
        update_fields = [
            "severity",
            "description",
            "annotations",
            "raw_payload",
            "context_key",
            "updated_at",
        ]

        # A re-push of something that had recovered arrives here, not in
        # _resolve_alert: _process_alert only special-cases firing -> resolved, so
        # resolved -> firing lands in this method. Reopening is not cosmetic. The
        # row's status is a ROUTING FACT (facts_from_alert), so an alert left
        # RESOLVED while its host is on fire matches the resolved lane and the
        # downstream run delivers an all-clear for a critical problem. Mirrors
        # AlertOrchestrator._update_alert, down to the `refired` event name, so one
        # incident reads the same however its alerts arrived.
        refired = alert.status == AlertStatus.RESOLVED and parsed.status == "firing"
        if refired:
            alert.status = AlertStatus.FIRING
            alert.ended_at = None
            update_fields += ["status", "ended_at"]

        alert.save(update_fields=update_fields)

        if refired:
            AlertHistory.objects.create(
                alert=alert,
                event="refired",
                old_status=AlertStatus.RESOLVED,
                new_status=AlertStatus.FIRING,
                details={"checker": (parsed.labels or {}).get("checker", "")},
            )

        if old_severity != parsed.severity:
            AlertHistory.objects.create(
                alert=alert,
                event="severity_changed",
                old_status=alert.status,
                new_status=alert.status,
                details={
                    "old_severity": old_severity,
                    "new_severity": parsed.severity,
                },
            )

        result.alerts_updated += 1
        logger.info(f"Updated alert from check: {alert.name}")

        result.alerts.append(alert)
        if is_material_change(
            old_severity=old_severity,
            new_severity=parsed.severity,
            old_status=old_status,
            new_status=parsed.status,
            old_key=old_key,
            new_key=new_key,
        ):
            result.material_alerts.append(alert)
        return alert

    def _resolve_alert(
        self,
        alert: Alert,
        parsed: ParsedAlert,
        result: ProcessingResult,
    ) -> Alert:
        """Resolve an existing alert."""
        alert.status = AlertStatus.RESOLVED
        alert.ended_at = parsed.ended_at or timezone.now()
        alert.save(update_fields=["status", "ended_at", "updated_at"])

        AlertHistory.objects.create(
            alert=alert,
            event="resolved",
            old_status=AlertStatus.FIRING,
            new_status=AlertStatus.RESOLVED,
            details={"resolved_by": "check"},
        )

        result.alerts_resolved += 1
        logger.info(f"Resolved alert from check: {alert.name}")

        result.alerts.append(alert)
        # This method is only reached on a FIRING -> RESOLVED transition, which the
        # design counts as material: the all-clear is what the resolved lane notifies on.
        result.material_alerts.append(alert)
        return alert

    def _check_incident_resolution(self):
        """Check if any incidents should be auto-resolved."""
        # Find open incidents where all alerts are resolved
        open_incidents = Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        ).prefetch_related("alerts")

        for incident in open_incidents:
            firing_alerts = incident.alerts.filter(status=AlertStatus.FIRING).count()
            if firing_alerts == 0:
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = timezone.now()
                incident.save(update_fields=["status", "resolved_at", "updated_at"])
                logger.info(f"Auto-resolved incident: {incident.title}")

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
        if checker_name not in CHECKER_REGISTRY:
            raise ValueError(
                f"Unknown checker: {checker_name}. "
                f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
            )

        checker_class = CHECKER_REGISTRY[checker_name]
        checker = checker_class(**(checker_kwargs or {}))
        check_result = checker.run(trace_id=self.trace_id)

        processing_result = self.process_check_result(check_result, labels)

        return check_result, processing_result

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
        result = CheckAlertResult()
        checker_configs = checker_configs or {}

        if checker_names is None:
            checker_names = list(CHECKER_REGISTRY.keys())

        for checker_name in checker_names:
            try:
                checker_kwargs = checker_configs.get(checker_name, {})
                check_result, processing_result = self.run_check_and_alert(
                    checker_name,
                    checker_kwargs=checker_kwargs,
                    labels=labels,
                )

                result.checks_run += 1
                result.alerts_created += processing_result.alerts_created
                result.alerts_updated += processing_result.alerts_updated
                result.alerts_resolved += processing_result.alerts_resolved
                result.incidents_created += processing_result.incidents_created
                result.incidents_updated += processing_result.incidents_updated
                result.alerts.extend(processing_result.alerts)
                result.material_alerts.extend(processing_result.material_alerts)

                if processing_result.has_errors:
                    result.errors.extend(processing_result.errors)

            except Exception as e:
                logger.exception(f"Error running checker {checker_name}")
                result.errors.append(f"{checker_name}: {str(e)}")

        return result
