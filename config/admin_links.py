"""URL and anchor builders shared by every admin surface.

A page that names an object has to link it, and hand-spelling
``format_html('<a href="{}">{}</a>', reverse(...), ...)`` in each admin module
is how links go missing. These four are the only place those URLs are built.

Cross-cutting, so it lives in ``config`` rather than in an app: every app's
admin reads it and none of them owns it (AGENTS.md, "App vs. utility test").
"""

from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode

DASH = "—"


def admin_url(obj):
    """Change-page URL for ``obj``, or ``None`` when there is no object."""
    if obj is None:
        return None
    meta = obj._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])


def admin_link(obj, label=None, *, empty=DASH):
    """Anchor to ``obj``'s change page, or ``empty`` when there is no object.

    ``label`` defaults to ``str(obj)`` and is always escaped: instance ids,
    checker names and channel names all arrive over a webhook.
    """
    if obj is None:
        return empty
    return format_html('<a href="{}">{}</a>', admin_url(obj), label if label is not None else obj)


def changelist_url(model, **filters):
    """Changelist URL for ``model``, with ``filters`` as its query string.

    Keys are sorted so the same filter set always spells the same URL.
    """
    meta = model._meta
    url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
    if not filters:
        return url
    return f"{url}?{urlencode(sorted(filters.items()))}"


def changelist_link(model, label, **filters):
    """Anchor to ``model``'s changelist, filtered by ``filters``."""
    return format_html('<a href="{}">{}</a>', changelist_url(model, **filters), label)
