from django import template

from apps.console.intents import ResolvedAction, admin_url, resolve_action
from config.describing import Action

register = template.Library()


@register.simple_tag
def resolved(action: Action | None) -> ResolvedAction | None:
    if not action:
        return None
    return resolve_action(action)


@register.simple_tag(name="admin_url")
def admin_url_tag(name: str) -> str:
    return admin_url(name)
