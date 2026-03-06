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
    "SlackNotifyDriver",
    "PagerDutyNotifyDriver",
    "EmailNotifyDriver",
    "GenericNotifyDriver",
]

# Registry of available notification drivers
DRIVER_REGISTRY: dict[str, type[BaseNotifyDriver]] = {
    "slack": SlackNotifyDriver,
    "pagerduty": PagerDutyNotifyDriver,
    "email": EmailNotifyDriver,
    "generic": GenericNotifyDriver,
}
