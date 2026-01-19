"""
Notification drivers for sending notifications to various platforms.
"""

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver

__all__ = [
    "NotificationMessage",
    "BaseNotifyDriver",
    "DRIVER_REGISTRY",
    "is_notify_enabled",
    "get_enabled_notify_drivers",
]

# Registry of available notification drivers
DRIVER_REGISTRY: dict[str, type[BaseNotifyDriver]] = {
    "slack": SlackNotifyDriver,
    "pagerduty": PagerDutyNotifyDriver,
    "email": EmailNotifyDriver,
    "generic": GenericNotifyDriver,
}


def is_notify_enabled(driver_name: str) -> bool:
    """
    Check if a notification driver is enabled.

    Disabled when:
    - NOTIFY_SKIP_ALL=True, or
    - driver_name is in NOTIFY_SKIP

    Args:
        driver_name: Name of the driver to check.

    Returns:
        True if the driver is enabled, False if skipped.
    """
    from django.conf import settings

    if getattr(settings, "NOTIFY_SKIP_ALL", False):
        return False

    skip_list = getattr(settings, "NOTIFY_SKIP", [])
    return driver_name not in skip_list


def get_enabled_notify_drivers() -> dict[str, type[BaseNotifyDriver]]:
    """
    Get registry of enabled notification drivers (excluding skipped ones).

    Returns:
        Dictionary of driver names to driver classes, excluding
        those listed in settings.NOTIFY_SKIP.
    """
    return {name: cls for name, cls in DRIVER_REGISTRY.items() if is_notify_enabled(name)}
