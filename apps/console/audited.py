"""What each console write records: the state of its row, its target and its secrets."""

from apps.console.audit import config_keys, config_state, diff, record, snapshot, unlisted
from apps.console.forms import UserEditForm
from apps.intelligence.forms import PROVIDER_FIELDS
from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.providers import PROVIDERS
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.forms import CHANNEL_FIELDS
from apps.notify.models import NotificationChannel
from apps.orchestration.forms import MODEL_FIELDS
from apps.orchestration.models import PipelineDefinition
from config.api_keys import KEY_FIELDS
from config.models import APIKey

ROUTE_FIELDS = tuple(f for f in MODEL_FIELDS if f != "channel") + ("stages", "match")
CHANNEL_SECRETS = config_keys(DRIVER_REGISTRY.values(), secret=True)
CHANNEL_PUBLIC = config_keys(DRIVER_REGISTRY.values(), secret=False)
PROVIDER_SECRETS = config_keys(PROVIDERS.values(), secret=True)
PROVIDER_PUBLIC = config_keys(PROVIDERS.values(), secret=False)
KEY_SECRETS = frozenset({"key"})
USER_FIELDS = tuple(UserEditForm.Meta.fields)
USER_SECRETS = frozenset({"password"})


def route_state(route: PipelineDefinition) -> dict:
    state = snapshot(route, ROUTE_FIELDS)
    state["channel"] = route.channel.name if route.channel else None
    return state


def route_target(route: PipelineDefinition) -> tuple[str, int, str]:
    return ("route", route.pk, route.name)


def route_changes(before: dict, after: dict) -> dict:
    return diff(before, after)


def routes_on(channel: NotificationChannel) -> list[PipelineDefinition]:
    routes = PipelineDefinition.objects.select_for_update().filter(channel=channel)
    return list(routes.order_by("pk"))


def record_routes_cut(request, routes: list[PipelineDefinition], channel_name: str) -> None:
    """Deleting a channel empties ``channel`` on its routes, so each one records an edit."""
    for route in routes:
        changes = {"channel": {"old": channel_name, "new": None}}
        record(request, "route.edit", route_target(route), changes=changes)


def channel_state(row: NotificationChannel) -> dict:
    fields = tuple(f for f in CHANNEL_FIELDS if f != "config")
    return snapshot(row, fields) | config_state(row.config)


def channel_target(row: NotificationChannel) -> tuple[str, int, str]:
    return ("channel", row.pk, row.name)


def channel_changes(before: dict, after: dict) -> dict:
    return diff(before, after, secret=CHANNEL_SECRETS | unlisted(before, after, CHANNEL_PUBLIC))


def provider_state(row: IntelligenceProvider) -> dict:
    fields = tuple(f for f in PROVIDER_FIELDS if f != "config")
    return snapshot(row, fields) | config_state(row.config)


def provider_target(row: IntelligenceProvider) -> tuple[str, int, str]:
    return ("provider", row.pk, row.name)


def provider_changes(before: dict, after: dict) -> dict:
    return diff(before, after, secret=PROVIDER_SECRETS | unlisted(before, after, PROVIDER_PUBLIC))


def others_on(pk: int | None) -> list[IntelligenceProvider]:
    on = IntelligenceProvider.objects.select_for_update().filter(is_active=True)
    return list((on if pk is None else on.exclude(pk=pk)).order_by("pk"))


def turned_off() -> dict:
    return {"is_active": {"old": True, "new": False}}


def record_turned_off(request, others: list[IntelligenceProvider]) -> None:
    """Turning one provider on turns the others off, so each one it turned off records an edit."""
    off = set(
        IntelligenceProvider.objects.filter(
            pk__in=[o.pk for o in others], is_active=False
        ).values_list("pk", flat=True)
    )
    for other in others:
        if other.pk in off:
            record(request, "provider.edit", provider_target(other), changes=turned_off())


def key_state(key: APIKey) -> dict:
    return snapshot(key, (*KEY_FIELDS, "key"))


def key_target(key: APIKey) -> tuple[str, int, str]:
    return ("key", key.pk, key.name)


def key_changes(before: dict, after: dict) -> dict:
    return diff(before, after, secret=KEY_SECRETS)


def user_state(user) -> dict:
    return snapshot(user, (*USER_FIELDS, "password"))


def user_target(user) -> tuple[str, int, str]:
    return ("user", user.pk, user.get_username())


def user_changes(before: dict, after: dict) -> dict:
    return diff(before, after, secret=USER_SECRETS)
