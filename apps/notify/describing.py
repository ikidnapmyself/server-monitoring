"""Notify deliveries in words, from the delivery dicts the notify stage snapshot stored.

Snapshots come from old rows too, so every field is read defensively and a describer never
raises on a shape it doesn't expect.
"""

from typing import Any

from config.describing import Action, Description, Fact

_EDIT_CHANNEL = "edit_channel"

FAILURES: dict[str, tuple[str, Action]] = {
    "auth": (
        "{driver} rejected the credentials",
        Action("Update the channel's credentials", intent=_EDIT_CHANNEL),
    ),
    "rate_limit": (
        "{driver} is rate limiting this hub",
        Action("Wait, or send to this channel less often"),
    ),
    "timeout": ("{driver} didn't answer in time", Action("Wait for the next run to try again")),
    "provider_down": ("{driver} is unreachable", Action("Check the service's status page")),
    "rejected": (
        "{driver} refused the message",
        Action("Check the channel's settings", intent=_EDIT_CHANNEL),
    ),
    "unknown": ("", Action("Check the channel's settings", intent=_EDIT_CHANNEL)),
}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _driver_label(provider: str) -> str:
    # Drivers declare no display name, so the registry key is all there is.
    return provider.replace("_", " ").capitalize()


def _channel(value: object) -> str:
    channel = value.strip() if isinstance(value, str) else ""
    return "the default channel" if channel in ("", "default") else channel


def _response_facts(response: object) -> tuple[Fact, ...]:
    if not isinstance(response, dict):
        return ()
    found: list[Fact] = []
    code = response.get("status_code")
    if isinstance(code, int) and not isinstance(code, bool):
        found.append(Fact("HTTP status", str(code)))
    error = _text(response.get("error")) or _text(response.get("message"))
    if error:
        found.append(Fact("Error", error))
    return tuple(found)


def describe_delivery(delivery: Any) -> Description:
    if not isinstance(delivery, dict):
        delivery = {}
    channel = _channel(delivery.get("channel"))
    provider = _text(delivery.get("provider"))
    label = _driver_label(provider)

    if delivery.get("status") == "success":
        headline = f"Sent to {channel} by {label}" if label else f"Sent to {channel}"
        message_id = _text(delivery.get("provider_id"))
        facts = (Fact("Message ID", message_id, copy=True),) if message_id else ()
        return Description(headline, "ok", facts=facts)

    kind = delivery.get("failure")
    reason, action = FAILURES.get(kind if isinstance(kind, str) else "", FAILURES["unknown"])
    headline = f"Not sent to {channel}"
    if reason:
        headline += ": " + reason.format(driver=label or "the provider")
    record = _text(delivery.get("driver"))
    if action.intent and record:
        action = Action(action.text, intent=action.intent, params={"name": record})
    return Description(
        headline, "critical", action=action, facts=_response_facts(delivery.get("response"))
    )


def describe_notify(snapshot: Any) -> tuple[Description, ...]:
    deliveries = snapshot.get("deliveries") if isinstance(snapshot, dict) else None
    if not isinstance(deliveries, list):
        deliveries = []
    described = tuple(describe_delivery(d) for d in deliveries if isinstance(d, dict))
    return described or (Description("Nothing was sent", "muted"),)
