"""
Alert orchestration services.

This module contains the business logic for processing incoming alerts,
creating/updating incidents, and managing alert lifecycle.
"""

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.alerts.drivers import (
    BaseAlertDriver,
    ParsedAlert,
    ParsedPayload,
    detect_driver,
    get_driver,
)
from apps.alerts.models import (
    Alert,
    AlertHistory,
    AlertSeverity,
    AlertStatus,
    Incident,
    IncidentStatus,
)


logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of processing an incoming alert payload."""

    alerts_created: int = 0
    alerts_updated: int = 0
    alerts_resolved: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def total_processed(self) -> int:
        return self.alerts_created + self.alerts_updated + self.alerts_resolved

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class AlertOrchestrator:
    """
    Orchestrates the processing of incoming alerts.

    This is the main entry point for alert ingestion. It:
    1. Detects or uses the specified driver to parse the payload
    2. Creates or updates Alert records
    3. Creates or updates Incidents based on alert grouping
    4. Records alert history for audit trail

    Usage:
        orchestrator = AlertOrchestrator()
        result = orchestrator.process_webhook(payload)
        # or with a specific driver:
        result = orchestrator.process_webhook(payload, driver="alertmanager")
    """

    def __init__(
        self,
        auto_create_incidents: bool = True,
        auto_resolve_incidents: bool = True,
    ):
        """
        Initialize the orchestrator.

        Args:
            auto_create_incidents: Automatically create incidents for new alerts.
            auto_resolve_incidents: Automatically resolve incidents when all alerts resolve.
        """
        self.auto_create_incidents = auto_create_incidents
        self.auto_resolve_incidents = auto_resolve_incidents

    def process_webhook(
        self,
        payload: dict[str, Any],
        driver: str | BaseAlertDriver | None = None,
    ) -> ProcessingResult:
        """
        Process an incoming webhook payload.

        Args:
            payload: Raw JSON payload from the webhook.
            driver: Driver name, instance, or None for auto-detection.

        Returns:
            ProcessingResult with counts of created/updated records.
        """
        result = ProcessingResult()

        try:
            # Get or detect driver
            driver_instance = self._get_driver(payload, driver)
            if not driver_instance:
                result.errors.append("Could not detect driver for payload")
                return result

            # Parse the payload
            parsed = driver_instance.parse(payload)

            # Process each alert
            with transaction.atomic():
                for parsed_alert in parsed.alerts:
                    self._process_alert(parsed_alert, parsed.source, result)

            # Handle incident auto-resolution
            if self.auto_resolve_incidents:
                self._check_incident_resolution()

        except Exception as e:
            logger.exception("Error processing webhook payload")
            result.errors.append(str(e))

        return result

    def _get_driver(
        self,
        payload: dict[str, Any],
        driver: str | BaseAlertDriver | None,
    ) -> BaseAlertDriver | None:
        """Get driver instance from name, instance, or auto-detect."""
        if driver is None:
            return detect_driver(payload)
        elif isinstance(driver, str):
            return get_driver(driver)
        elif isinstance(driver, BaseAlertDriver):
            return driver
        else:
            raise ValueError(f"Invalid driver type: {type(driver)}")

    def _process_alert(
        self,
        parsed: ParsedAlert,
        source: str,
        result: ProcessingResult,
    ) -> Alert:
        """Process a single parsed alert."""
        # Check if alert already exists (by fingerprint and source)
        existing = Alert.objects.filter(
            fingerprint=parsed.fingerprint,
            source=source,
        ).first()

        if existing:
            return self._update_alert(existing, parsed, result)
        else:
            return self._create_alert(parsed, source, result)

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
            status=parsed.status,
            description=parsed.description,
            labels=parsed.labels,
            annotations=parsed.annotations,
            raw_payload=parsed.raw_payload,
            started_at=parsed.started_at,
            ended_at=parsed.ended_at,
        )

        # Record history
        AlertHistory.objects.create(
            alert=alert,
            event="created",
            new_status=parsed.status,
            details={"source": source},
        )

        result.alerts_created += 1
        logger.info(f"Created alert: {alert.name} ({alert.fingerprint})")

        # Auto-create incident if enabled
        if self.auto_create_incidents and parsed.status == "firing":
            self._create_or_attach_incident(alert, result)

        return alert

    def _update_alert(
        self,
        alert: Alert,
        parsed: ParsedAlert,
        result: ProcessingResult,
    ) -> Alert:
        """Update an existing alert with new data."""
        old_status = alert.status

        # Update fields
        alert.name = parsed.name
        alert.severity = parsed.severity
        alert.description = parsed.description
        alert.labels = parsed.labels
        alert.annotations = parsed.annotations
        alert.raw_payload = parsed.raw_payload

        # Handle status change
        if parsed.status != old_status:
            alert.status = parsed.status

            if parsed.status == "resolved":
                alert.ended_at = parsed.ended_at or timezone.now()
                result.alerts_resolved += 1
                event = "resolved"
            else:
                alert.ended_at = None
                event = "refired"

            # Record history
            AlertHistory.objects.create(
                alert=alert,
                event=event,
                old_status=old_status,
                new_status=parsed.status,
            )

            logger.info(f"Alert {event}: {alert.name} ({alert.fingerprint})")
        else:
            result.alerts_updated += 1

        alert.save()
        return alert

    def _create_or_attach_incident(
        self,
        alert: Alert,
        result: ProcessingResult,
    ) -> Incident:
        """Create a new incident or attach alert to existing one."""
        # Look for existing open incident with same alert name
        existing_incident = Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
            alerts__name=alert.name,
        ).first()

        if existing_incident:
            # Attach to existing incident
            alert.incident = existing_incident
            alert.save(update_fields=["incident"])

            # Update incident severity if this alert is more severe
            if self._severity_rank(alert.severity) > self._severity_rank(existing_incident.severity):
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
        logger.info(f"Created incident: {incident.title}")

        return incident

    def _check_incident_resolution(self):
        """Check if any incidents should be auto-resolved."""
        # Find open incidents where all alerts are resolved
        open_incidents = Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        )

        for incident in open_incidents:
            firing_count = incident.alerts.filter(status=AlertStatus.FIRING).count()
            if firing_count == 0 and incident.alerts.exists():
                incident.resolve(summary="All alerts resolved automatically")
                logger.info(f"Auto-resolved incident: {incident.title}")

    def _severity_rank(self, severity: str) -> int:
        """Return numeric rank for severity comparison."""
        ranks = {
            AlertSeverity.INFO: 1,
            AlertSeverity.WARNING: 2,
            AlertSeverity.CRITICAL: 3,
            "info": 1,
            "warning": 2,
            "critical": 3,
        }
        return ranks.get(severity, 0)


class IncidentManager:
    """
    Service for managing incidents.

    Provides methods for incident lifecycle management beyond
    what the orchestrator handles automatically.
    """

    @staticmethod
    def acknowledge(incident_id: int, acknowledged_by: str = "") -> Incident:
        """
        Acknowledge an incident.

        Args:
            incident_id: ID of the incident to acknowledge.
            acknowledged_by: Optional identifier of who acknowledged.

        Returns:
            Updated incident.
        """
        incident = Incident.objects.get(pk=incident_id)
        incident.acknowledge()

        if acknowledged_by:
            incident.metadata["acknowledged_by"] = acknowledged_by
            incident.save(update_fields=["metadata"])

        logger.info(f"Incident acknowledged: {incident.title}")
        return incident

    @staticmethod
    def resolve(incident_id: int, summary: str = "", resolved_by: str = "") -> Incident:
        """
        Resolve an incident.

        Args:
            incident_id: ID of the incident to resolve.
            summary: Resolution summary.
            resolved_by: Optional identifier of who resolved.

        Returns:
            Updated incident.
        """
        incident = Incident.objects.get(pk=incident_id)
        incident.resolve(summary=summary)

        if resolved_by:
            incident.metadata["resolved_by"] = resolved_by
            incident.save(update_fields=["metadata"])

        logger.info(f"Incident resolved: {incident.title}")
        return incident

    @staticmethod
    def close(incident_id: int) -> Incident:
        """
        Close a resolved incident.

        Args:
            incident_id: ID of the incident to close.

        Returns:
            Updated incident.
        """
        incident = Incident.objects.get(pk=incident_id)
        incident.close()

        logger.info(f"Incident closed: {incident.title}")
        return incident

    @staticmethod
    def add_note(incident_id: int, note: str, author: str = "") -> Incident:
        """
        Add a note to an incident.

        Args:
            incident_id: ID of the incident.
            note: Note text.
            author: Optional author identifier.

        Returns:
            Updated incident.
        """
        incident = Incident.objects.get(pk=incident_id)

        if "notes" not in incident.metadata:
            incident.metadata["notes"] = []

        incident.metadata["notes"].append({
            "text": note,
            "author": author,
            "timestamp": timezone.now().isoformat(),
        })

        incident.save(update_fields=["metadata", "updated_at"])
        return incident

    @staticmethod
    def get_open_incidents():
        """Get all open incidents."""
        return Incident.objects.filter(
            status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]
        ).prefetch_related("alerts")

    @staticmethod
    def get_incident_with_alerts(incident_id: int) -> Incident:
        """Get an incident with all its alerts."""
        return Incident.objects.prefetch_related(
            "alerts", "alerts__history"
        ).get(pk=incident_id)


class AlertQueryService:
    """
    Service for querying alerts.
    """

    @staticmethod
    def get_firing_alerts():
        """Get all currently firing alerts."""
        return Alert.objects.filter(status=AlertStatus.FIRING)

    @staticmethod
    def get_alerts_by_severity(severity: str):
        """Get alerts filtered by severity."""
        return Alert.objects.filter(severity=severity)

    @staticmethod
    def get_alerts_by_source(source: str):
        """Get alerts from a specific source."""
        return Alert.objects.filter(source=source)

    @staticmethod
    def get_recent_alerts(hours: int = 24):
        """Get alerts from the last N hours."""
        since = timezone.now() - timezone.timedelta(hours=hours)
        return Alert.objects.filter(received_at__gte=since)

    @staticmethod
    def get_alert_with_history(alert_id: int) -> Alert:
        """Get an alert with its history."""
        return Alert.objects.prefetch_related("history").get(pk=alert_id)

