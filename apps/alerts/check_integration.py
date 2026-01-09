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

from apps.alerts.drivers.base import ParsedAlert, ParsedPayload
from apps.alerts.models import (
    Alert,
    AlertHistory,
    AlertSeverity,
    AlertStatus,
    Incident,
    IncidentStatus,
)
from apps.alerts.services import AlertOrchestrator, ProcessingResult
from apps.checkers.checkers import CHECKER_REGISTRY, CheckResult, CheckStatus

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
    ):
        """
        Initialize the bridge.

        Args:
            auto_create_incidents: Automatically create incidents for critical alerts.
            auto_resolve_incidents: Automatically resolve incidents when alerts resolve.
            hostname: Override hostname for alert labels. Defaults to system hostname.
        """
        self.orchestrator = AlertOrchestrator(
            auto_create_incidents=auto_create_incidents,
            auto_resolve_incidents=auto_resolve_incidents,
        )
        self.hostname = hostname or socket.gethostname()

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
        """Create a new alert from parsed data."""
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
        )

        AlertHistory.objects.create(
            alert=alert,
            event="created",
            new_status=AlertStatus.FIRING,
            details={"source": source, "checker": parsed.labels.get("checker", "")},
        )

        result.alerts_created += 1
        logger.info(f"Created alert from check: {alert.name} ({alert.fingerprint})")

        # Auto-create incident if enabled and this is critical/warning
        if self.orchestrator.auto_create_incidents and parsed.severity in (
            AlertSeverity.CRITICAL,
            AlertSeverity.WARNING,
        ):
            self._create_or_attach_incident(alert, result)

        return alert

    def _update_alert(
        self,
        alert: Alert,
        parsed: ParsedAlert,
        result: ProcessingResult,
    ) -> Alert:
        """Update an existing alert with new data."""
        old_severity = alert.severity

        alert.severity = parsed.severity
        alert.description = parsed.description
        alert.annotations = parsed.annotations
        alert.raw_payload = parsed.raw_payload
        alert.save(update_fields=[
            "severity",
            "description",
            "annotations",
            "raw_payload",
            "updated_at",
        ])

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

        return alert

    def _create_or_attach_incident(
        self,
        alert: Alert,
        result: ProcessingResult,
    ) -> Incident | None:
        """Create a new incident or attach alert to existing one."""
        # Look for existing open incident with same checker
        checker_name = alert.labels.get("checker", "")
        existing_incident = Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
            alerts__labels__checker=checker_name,
            alerts__labels__hostname=alert.labels.get("hostname", ""),
        ).first()

        if existing_incident:
            alert.incident = existing_incident
            alert.save(update_fields=["incident"])

            # Update severity if this is more severe
            severity_rank = {"critical": 3, "warning": 2, "info": 1}
            if severity_rank.get(alert.severity, 0) > severity_rank.get(
                existing_incident.severity, 0
            ):
                existing_incident.severity = alert.severity
                existing_incident.save(update_fields=["severity", "updated_at"])

            result.incidents_updated += 1
            return existing_incident

        # Create new incident
        incident = Incident.objects.create(
            title=alert.name,
            severity=alert.severity,
            description=alert.description,
        )

        alert.incident = incident
        alert.save(update_fields=["incident"])

        result.incidents_created += 1
        logger.info(f"Created incident from check: {incident.title}")

        return incident

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
            ValueError: If checker_name is not in the registry.
        """
        if checker_name not in CHECKER_REGISTRY:
            raise ValueError(
                f"Unknown checker: {checker_name}. "
                f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
            )

        checker_class = CHECKER_REGISTRY[checker_name]
        checker = checker_class(**(checker_kwargs or {}))
        check_result = checker.check()

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

                if processing_result.has_errors:
                    result.errors.extend(processing_result.errors)

            except Exception as e:
                logger.exception(f"Error running checker {checker_name}")
                result.errors.append(f"{checker_name}: {str(e)}")

        return result

