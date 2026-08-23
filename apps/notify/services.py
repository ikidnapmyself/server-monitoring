"""Notification selection utilities.

Centralizes logic for resolving which notify provider and configuration to use
based on payloads, CLI args, or DB-stored NotificationChannel records.

Selection priority:
- If `provider_arg` matches a NotificationChannel.name (and is active), use that
  channel's driver and stored config.
- If `provider_arg` is not provided AND the caller opted in with
  `allow_default_channel=True`, select the first active NotificationChannel
  ordered by name and use its driver/config. Without the opt-in no channel is
  selected: for the pipeline that default would be a guess about routing.
- Otherwise treat `provider_arg` as a provider key (e.g. 'slack') and use the
  provided payload_config.

This utility returns (provider_name, config, selected_label, driver_class)
where `selected_label` is either the channel name (if chosen from DB) or the
provider key.
"""

from __future__ import annotations

from apps.notify.models import NotificationChannel
from apps.notify.views import DRIVER_REGISTRY


class NotifySelector:
    """Resolve notify provider and configuration.

    Methods are intentionally simple and synchronous; callers are responsible
    for instantiating driver classes and performing validation/send operations.
    """

    @staticmethod
    def resolve(
        provider_arg: str | None,
        payload_config: dict | None = None,
        requested_channel: str | None = None,
        allow_default_channel: bool = False,
    ) -> tuple[str, dict, str, type | None, NotificationChannel | None, str]:
        """Resolve provider name, config, selected label, driver class, channel object, and final channel.

        Args:
            provider_arg: Optional provider key or channel name provided by caller.
            payload_config: Optional dict containing payload-supplied config.
            requested_channel: Optional hint for destination channel (e.g., Slack channel).
            allow_default_channel: Opt in to the single-channel default when no
                provider is named. Interactive callers ("send a test message") mean
                it; the pipeline does not.

        Returns:
            (provider_name, config, selected_label, driver_class, channel_obj, final_channel)
        """
        payload_config = payload_config or {}

        channel_obj: NotificationChannel | None = None

        # Try DB channel lookup when a provider arg is given
        if provider_arg:
            channel = NotificationChannel.objects.filter(name=provider_arg, is_active=True).first()
            if channel:
                provider_name = channel.driver
                config = channel.config or {}
                selected_label = channel.name
                channel_obj = channel
            else:
                # Treat provider_arg directly as provider key
                provider_name = provider_arg or "generic"
                config = payload_config
                selected_label = provider_arg

        else:
            # The single-channel default is opt-in. For an interactive caller
            # ("send a test message") it is the intent; for the pipeline it would be
            # a guess about routing, and picking by name is how a critical alert
            # ends up in whatever channel sorts first. See
            # docs/plans/2026-08-22-lane-channel-required-design.md.
            channel = (
                NotificationChannel.objects.filter(is_active=True).order_by("name").first()
                if allow_default_channel
                else None
            )
            if channel:
                provider_name = channel.driver
                config = channel.config or {}
                selected_label = channel.name
                channel_obj = channel
            else:
                # Fallback to generic driver key and given payload config
                provider_name = "generic"
                config = payload_config
                selected_label = provider_name

        driver_class = DRIVER_REGISTRY.get(provider_name)

        # Determine the final channel. Priority:
        # 1) If we selected a DB NotificationChannel, prefer its stored channel (if any).
        # 2) Else if the caller passed an explicit requested_channel, use that.
        # 3) Else fall back to provider config's "channel" value.
        # 4) Finally default to 'default'.
        if channel_obj:
            final_channel = (channel_obj.config or {}).get("channel") or payload_config.get(
                "channel", "default"
            )
        else:
            final_channel = (
                requested_channel
                if requested_channel is not None
                else payload_config.get("channel", "default")
            )

        # At this point provider_name is guaranteed to be a str (we default to "generic").
        return provider_name, config, selected_label, driver_class, channel_obj, final_channel
