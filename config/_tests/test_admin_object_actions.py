"""Every admin object-action button is POST-only unless it only previews on GET.

django-object-actions allows GET by default and puts its URLs behind staff login alone.
A GET that writes can be fired by any link a logged-in staff user follows.
"""

from django.contrib import admin

# These render a preview on GET and write only on a confirmed POST, after checking
# the change permission themselves.
PREVIEW_ON_GET = {("node", "reevaluate_open_alerts")}


def _object_actions():
    for model, model_admin in admin.site._registry.items():
        for name in getattr(model_admin, "change_actions", ()):
            yield model._meta.model_name, name, getattr(model_admin, name)


def test_there_are_object_actions_to_check():
    assert len(list(_object_actions())) >= len(PREVIEW_ON_GET)


def test_writing_object_actions_refuse_get():
    for model_name, name, action in _object_actions():
        if (model_name, name) in PREVIEW_ON_GET:
            continue
        assert getattr(action, "methods", None) == ("POST",), f"{model_name}.{name} accepts GET"
        assert getattr(action, "button_type", None) == "form", f"{model_name}.{name} is a link"
