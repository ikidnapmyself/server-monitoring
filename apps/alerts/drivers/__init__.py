"""
Alert drivers for ingesting alerts from various sources.
"""

from apps.alerts.drivers.alertmanager import AlertManagerDriver
from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload
from apps.alerts.drivers.cluster import ClusterDriver
from apps.alerts.drivers.datadog import DatadogDriver
from apps.alerts.drivers.generic import GenericWebhookDriver
from apps.alerts.drivers.grafana import GrafanaDriver
from apps.alerts.drivers.internal import InternalDriver
from apps.alerts.drivers.newrelic import NewRelicDriver
from apps.alerts.drivers.opsgenie import OpsGenieDriver
from apps.alerts.drivers.pagerduty import PagerDutyDriver
from apps.alerts.drivers.zabbix import ZabbixDriver

__all__ = [
    "BaseAlertDriver",
    "ParsedAlert",
    "ParsedPayload",
    "AlertManagerDriver",
    "ClusterDriver",
    "GrafanaDriver",
    "GenericWebhookDriver",
    "InternalDriver",
    "PagerDutyDriver",
    "DatadogDriver",
    "NewRelicDriver",
    "OpsGenieDriver",
    "ZabbixDriver",
    "DRIVER_REGISTRY",
    "WEBHOOK_DRIVERS",
    "get_driver",
    "detect_driver",
]

# Registry of available drivers (order matters for detection)
DRIVER_REGISTRY: dict[str, type[BaseAlertDriver]] = {
    "alertmanager": AlertManagerDriver,
    "grafana": GrafanaDriver,
    "pagerduty": PagerDutyDriver,
    "datadog": DatadogDriver,
    "newrelic": NewRelicDriver,
    "opsgenie": OpsGenieDriver,
    "zabbix": ZabbixDriver,
    "generic": GenericWebhookDriver,
    # In-process only — intentionally excluded from WEBHOOK_DRIVERS below
    # so it cannot be reached via /alerts/webhook/.
    "internal": InternalDriver,
}

# Drivers reachable from /alerts/webhook/. Drivers listed in DRIVER_REGISTRY
# but explicitly excluded here can only be invoked by in-process callers
# (e.g. ``orchestrator.process_webhook(payload, driver="internal")``).
_NON_WEBHOOK_DRIVERS: frozenset[str] = frozenset({"internal"})
WEBHOOK_DRIVERS: set[str] = {name for name in DRIVER_REGISTRY if name not in _NON_WEBHOOK_DRIVERS}


# Register cluster driver only when CLUSTER_ENABLED=1
def _register_cluster_driver():
    from django.conf import settings

    if getattr(settings, "CLUSTER_ENABLED", False):
        DRIVER_REGISTRY["cluster"] = ClusterDriver
        if "cluster" not in _NON_WEBHOOK_DRIVERS:
            WEBHOOK_DRIVERS.add("cluster")


_register_cluster_driver()


def get_driver(name: str) -> BaseAlertDriver:
    """
    Get a driver instance by name.

    Args:
        name: Driver name (e.g., "alertmanager", "grafana", "generic", "internal").

    Returns:
        Driver instance.

    Raises:
        ValueError: If driver name is not found.
    """
    if name not in DRIVER_REGISTRY:
        raise ValueError(f"Unknown driver: {name}. Available: {', '.join(DRIVER_REGISTRY.keys())}")
    return DRIVER_REGISTRY[name]()


def detect_driver(payload: dict) -> BaseAlertDriver | None:
    """
    Auto-detect the appropriate driver for a payload.

    Tries each webhook-reachable driver's validate() method in order and
    returns the first match. The generic driver is tried last as it accepts
    most payloads. Non-webhook drivers (e.g. ``InternalDriver``) are
    intentionally excluded: a crafted external payload must not be able to
    auto-select an in-process-only driver.

    Args:
        payload: Raw webhook payload.

    Returns:
        Matching driver instance, or None if no driver matches.
    """
    # Try specific drivers first (webhook-reachable only).
    for name, driver_class in DRIVER_REGISTRY.items():
        if name == "generic":
            continue  # Try generic last
        if name not in WEBHOOK_DRIVERS:
            continue  # Skip in-process-only drivers (e.g. "internal")
        driver = driver_class()
        if driver.validate(payload):
            return driver

    # Fall back to generic driver (also webhook-reachable).
    if "generic" in WEBHOOK_DRIVERS:
        generic = GenericWebhookDriver()
        if generic.validate(payload):
            return generic

    return None
