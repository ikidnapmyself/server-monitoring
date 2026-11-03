from django import template

from apps.console.intents import ResolvedAction, resolve_action
from config.describing import Action

register = template.Library()


@register.simple_tag
def resolved(action: Action | None) -> ResolvedAction | None:
    if not action:
        return None
    return resolve_action(action)
