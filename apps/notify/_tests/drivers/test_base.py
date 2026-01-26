"""Tests for BaseNotifyDriver helpers and NotificationMessage."""

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage


class DummyDriver(BaseNotifyDriver):
    name = "dummy"

    def validate_config(self, config: dict[str, object]) -> bool:
        return True

    def send(self, message: NotificationMessage, config: dict[str, object]) -> dict[str, object]:
        return {"success": True}


def test_notification_message_normalization():
    msg = NotificationMessage(title="T", message="M", severity="CRITICAL")
    assert msg.severity == "critical"

    msg2 = NotificationMessage(title="T2", message="M2", severity="unknown")
    assert msg2.severity == "info"


def test_message_to_dict_basic():
    d = DummyDriver()
    msg = NotificationMessage(title="T", message="M", severity="warning")
    dd = d._message_to_dict(msg)
    assert dd["title"] == "T"
    assert dd["severity"] == "warning"
