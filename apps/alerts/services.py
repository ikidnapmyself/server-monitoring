"""
Alert orchestration services.

This module contains the business logic for processing incoming alerts,
creating/updating incidents, and managing alert lifecycle.
"""

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.alerts.context_keys import context_key_for
from apps.alerts.drivers import (
    BaseAlertDriver,
    ParsedAlert,
    detect_driver,
    get_driver,
)
from apps.alerts.materiality import is_material_change
from apps.alerts.models import (
    Alert,
    AlertHistory,
    AlertSeverity,
    AlertStatus,
    Incident,
    IncidentStatus,
)

logger = logging.getLogger(__name__)


def resolve_node(labels: dict | None):
    """Return the Node matching an ``instance_id`` label, or None.

    Only links to an already-registered node (``Node.upsert`` on the cluster push
    owns creation); a missing label or unknown node leaves the alert unlinked.
    """
    from apps.alerts.models import Node

    instance_id = (labels or {}).get("instance_id")
    if not instance_id:
        return None
    return Node.objects.filter(instance_id=instance_id).first()


def register_pushing_node(payload: dict[str, Any], driver: "str | BaseAlertDriver | None" = None):
    """Register/refresh the sending node for a cluster push (the agent registry).

    A cluster push proves the sender is alive, so its Node is upserted synchronously
    — at webhook time — independent of when the payload's alerts are drained. Returns
    the Node, or None if this isn't an identifiable cluster push (wrong source/driver
    or no ``instance_id``). Idempotent: shared by the webhook view and the drain path.
    """
    from apps.alerts.models import Node

    is_cluster = (driver == "cluster") or (payload.get("source") == "cluster")
    if not is_cluster:
        return None
    instance_id = payload.get("instance_id")
    if not instance_id:
        return None
    return Node.upsert(
        instance_id=instance_id,
        hostname=payload.get("hostname", ""),
        source="cluster",
    )


def instance_key_from_labels(labels: dict | None) -> str:
    """Instance/host key from a label dict, with source-specific fallthrough.

    Label keys differ by source (cluster: instance_id; Prometheus: instance;
    datadog/checker: hostname), so fall back through them. Empty string when none
    is present. Guards against non-dict input (e.g. attacker-controlled webhook
    payloads where ``labels`` is a string) by treating anything non-dict as empty.
    """
    labels = labels if isinstance(labels, dict) else {}
    return labels.get("instance_id") or labels.get("instance") or labels.get("hostname") or ""


def incident_instance_key(alert) -> str:
    """Instance/host an alert belongs to, for incident grouping.

    Delegates to :func:`instance_key_from_labels`. Empty string when no instance
    label is present (grouping then falls back to name-only).
    """
    return instance_key_from_labels(alert.labels)


# Read-only: this is module state now, so a stray mutation would be global and
# silent. Untrusted severities only ever reach it via severity_rank()'s .get().
_SEVERITY_RANK = MappingProxyType(
    {
        AlertSeverity.INFO: 1,
        AlertSeverity.WARNING: 2,
        AlertSeverity.CRITICAL: 3,
        "info": 1,
        "warning": 2,
        "critical": 3,
    }
)


def severity_rank(severity: str) -> int:
    """Numeric rank for severity comparison; unknown severities rank lowest."""
    return _SEVERITY_RANK.get(severity, 0)


@dataclass
class ProcessingResult:
    """Result of processing an incoming alert payload."""

    alerts_created: int = 0
    alerts_updated: int = 0
    alerts_resolved: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    errors: list[str] = field(default_factory=list)
    # Alert rows this call created or updated — the only alerts a caller may
    # route on (a global "latest alert" query would cross pushes/nodes).
    #
    # Retention is bounded to a single push: these are live model instances
    # carrying raw_payload/labels/annotations, so the list is O(alerts in this
    # payload), not a growing buffer. Two traps for consumers:
    #   - a payload repeating a fingerprint appends the same row twice, so
    #     anything that counts (rather than min/max) must dedupe first;
    #   - _check_incident_resolution() runs after the loop and mutates Incident
    #     rows, so a cached .incident on a retained alert may be stale.
    alerts: list[Alert] = field(default_factory=list)

    # Alerts whose write deserves its own downstream pipeline run — see
    # apps.alerts.materiality. Populated as alerts are written, because by the time
    # a caller sees this list the old severity/status/context_key are already gone.
    # A subset of `alerts`, and subject to the same two traps documented above.
    material_alerts: list[Alert] = field(default_factory=list)

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
        trace_id: str = "",
        create_from_resolved: bool = True,
    ):
        """
        Initialize the orchestrator.

        Args:
            auto_create_incidents: Automatically create incidents for new alerts.
            auto_resolve_incidents: Automatically resolve incidents when all alerts resolve.
            trace_id: Correlation ID stamped on alerts created by this run.
            create_from_resolved: Open a row for a first sighting that is already
                resolved. True for webhook traffic, where a resolved notification for
                an unseen alert is still a record. False for the checker bridge, whose
                sources report every checker every tick — a healthy one would otherwise
                open a resolved row on its first run.
        """
        self.auto_create_incidents = auto_create_incidents
        self.auto_resolve_incidents = auto_resolve_incidents
        self.trace_id = trace_id
        self.create_from_resolved = create_from_resolved

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

            # Cluster pushes register/refresh the sending node. The webhook already
            # does this synchronously at push time; the drain repeats it (idempotent)
            # so a node still refreshes even if a run is drained without a live push.
            register_pushing_node(payload, driver)

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
    ) -> Alert | None:
        """Process a single parsed alert. None when nothing was recorded."""
        from apps.alerts.reevaluation import reevaluate_severity

        # Recompute severity/status against the sending node's per-checker policy
        # (Node.config) before create/update. Fail-open: passthrough on any gap.
        parsed = reevaluate_severity(parsed)

        # Check if alert already exists (by fingerprint and source)
        existing = Alert.objects.filter(
            fingerprint=parsed.fingerprint,
            source=source,
        ).first()

        if existing and existing.status == AlertStatus.RESOLVED and parsed.status == "resolved":
            # Nothing to record: a re-push of something already quiet. Nodes push OK
            # results every tick (push_to_hub.py:112), and running those through
            # _update_alert wrote an `updated` AlertHistory row each time — ~30k rows
            # a day across a healthy fleet, none of which says anything. Never
            # material either, so no downstream run was ever involved. The FIRST
            # resolve is a status transition and does not come through here.
            return existing

        if existing:
            return self._update_alert(existing, parsed, result)
        if parsed.status == AlertStatus.RESOLVED and not self.create_from_resolved:
            return None
        return self._create_alert(parsed, source, result)

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
            status=parsed.status,
            description=parsed.description,
            labels=parsed.labels,
            annotations=parsed.annotations,
            raw_payload=parsed.raw_payload,
            started_at=parsed.started_at,
            ended_at=parsed.ended_at,
            trace_id=self.trace_id,
            node=resolve_node(parsed.labels),
            context_key=context_key_for(
                (parsed.labels or {}).get("checker", ""), parsed.annotations
            ),
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

        result.alerts.append(alert)
        # A brand-new alert is always material: there is no prior state to compare.
        result.material_alerts.append(alert)
        return alert

    # Fields compared on re-fire. raw_payload (noisy/large) and name
    # (fingerprint-stable) are deliberately excluded — see design doc.
    _DIFF_FIELDS = ("severity", "description", "labels", "annotations")

    def _diff_alert(self, alert: Alert, parsed: ParsedAlert) -> dict:
        """Return {field: [old, new]} for meaningful fields that changed on re-fire."""
        diff: dict = {}
        for field_name in self._DIFF_FIELDS:
            old = getattr(alert, field_name)
            new = getattr(parsed, field_name)
            if old != new:
                diff[field_name] = [old, new]
        return diff

    def _update_alert(
        self,
        alert: Alert,
        parsed: ParsedAlert,
        result: ProcessingResult,
    ) -> Alert:
        """Update an existing alert with new data."""
        old_status = alert.status
        old_severity = alert.severity
        old_key = alert.context_key
        new_key = context_key_for((parsed.labels or {}).get("checker", ""), parsed.annotations)

        # Snapshot what changed BEFORE overwriting fields below.
        changed = self._diff_alert(alert, parsed)

        # Update fields
        alert.name = parsed.name
        alert.severity = parsed.severity
        alert.description = parsed.description
        alert.labels = parsed.labels
        alert.annotations = parsed.annotations
        alert.raw_payload = parsed.raw_payload
        alert.context_key = new_key

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
                # The incident must follow the alert. An alert row is reused per
                # fingerprint, so its FK still points at the incident that was
                # resolved — and _find_open_incident only considers OPEN/ACKNOWLEDGED,
                # so nothing else would ever revisit it. Left alone, a FIRING alert
                # sits under a RESOLVED incident: notify reports an incident marked
                # resolved, and the admin contradicts itself.
                # A sibling alert with the same (name, instance) may have opened
                # its own incident while ours was resolved — _find_open_incident
                # ignores RESOLVED/CLOSED rows. Join it rather than reopening ours:
                # one situation is one open incident, and the alert must end up
                # under it either way, because a FIRING alert pointing at a resolved
                # incident is the invariant this whole branch exists to hold (and is
                # what the downstream run would be enqueued against).
                sibling = self._find_open_incident(alert)
                incident = alert.incident
                if sibling is not None:
                    self._attach_to_incident(alert, sibling, result)
                elif incident is not None and incident.status in (
                    IncidentStatus.RESOLVED,
                    IncidentStatus.CLOSED,
                ):
                    incident.reopen()
                elif incident is None and self.auto_create_incidents:
                    # An alert first seen RESOLVED never got an incident:
                    # _create_alert only attaches one to a firing alert, and a node
                    # reports every checker every tick, so a healthy one is first
                    # seen resolved. Fan-out routes on incident ids
                    # (routing.material_incident_ids drops incident-less alerts), so
                    # without this the checker's eventual CRITICAL produced no
                    # downstream run at all — no lane, no analysis, no message.
                    self._create_or_attach_incident(alert, result)

            AlertHistory.objects.create(
                alert=alert,
                event=event,
                old_status=old_status,
                new_status=parsed.status,
                details={"changed": changed},
            )

            logger.info(f"Alert {event}: {alert.name} ({alert.fingerprint})")
        else:
            result.alerts_updated += 1
            AlertHistory.objects.create(
                alert=alert,
                event="updated",
                old_status=old_status,
                new_status=alert.status,
                details={"changed": changed},
            )

        alert.save()
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

    def _create_or_attach_incident(
        self,
        alert: Alert,
        result: ProcessingResult,
    ) -> Incident:
        """Create a new incident or attach the alert to an existing one.

        Grouping is scoped to (name, instance): an open incident already holding an
        alert with the same name AND same instance (see ``incident_instance_key``).
        This keeps per-host incidents distinct — two servers firing the same alert
        are two incidents, not one.
        """
        existing_incident = self._find_open_incident(alert)

        if existing_incident:
            return self._attach_to_incident(alert, existing_incident, result)

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

    def _attach_to_incident(
        self,
        alert: Alert,
        incident: Incident,
        result: ProcessingResult,
    ) -> Incident:
        """Point the alert at this incident, escalating its severity if needed.

        Shared by the create path and the refire path so "join an existing
        incident" means one thing, severity escalation included.
        """
        alert.incident = incident
        alert.save(update_fields=["incident"])

        if self._severity_rank(alert.severity) > self._severity_rank(incident.severity):
            incident.severity = alert.severity
            incident.save(update_fields=["severity", "updated_at"])

        result.incidents_updated += 1
        return incident

    def _find_open_incident(self, alert) -> "Incident | None":
        """Open incident already holding an alert with this (name, instance)."""
        instance = incident_instance_key(alert)
        candidates = (
            Incident.objects.filter(
                status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
                alerts__name=alert.name,
            )
            .distinct()
            .prefetch_related("alerts")
        )
        for incident in candidates:
            for existing in incident.alerts.all():
                if existing.name == alert.name and incident_instance_key(existing) == instance:
                    return incident
        return None

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
        return severity_rank(severity)


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

        incident.metadata["notes"].append(
            {
                "text": note,
                "author": author,
                "timestamp": timezone.now().isoformat(),
            }
        )

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
        return Incident.objects.prefetch_related("alerts", "alerts__history").get(pk=incident_id)


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
        from datetime import timedelta

        since = timezone.now() - timedelta(hours=hours)
        return Alert.objects.filter(received_at__gte=since)

    @staticmethod
    def get_alert_with_history(alert_id: int) -> Alert:
        """Get an alert with its history."""
        return Alert.objects.prefetch_related("history").get(pk=alert_id)
