from django.contrib.auth.views import LoginView, LogoutView

from apps.console.urls import urlpatterns
from apps.console.views.base import ConsoleView

# Signing in and out must work for someone the console refuses, so these two aren't
# ConsoleViews. Each is exempt by name, and nothing else may be.
SIGN_IN_AND_OUT = {"login": LoginView, "logout": LogoutView}


def test_every_route_is_a_console_view_on_its_one_dispatch():
    views = [
        pattern.callback.view_class
        for pattern in urlpatterns
        if pattern.name not in SIGN_IN_AND_OUT
    ]

    assert views
    assert [view.__name__ for view in views if not issubclass(view, ConsoleView)] == []
    assert [
        klass.__name__
        for view in views
        for klass in view.__mro__[: view.__mro__.index(ConsoleView)]
        if "dispatch" in vars(klass)
    ] == []


def test_only_sign_in_and_out_are_exempt():
    exempt = {
        pattern.name: pattern.callback.view_class
        for pattern in urlpatterns
        if pattern.name in SIGN_IN_AND_OUT
    }

    assert set(exempt) == set(SIGN_IN_AND_OUT)
    for name, base in SIGN_IN_AND_OUT.items():
        assert issubclass(exempt[name], base), name
        assert not issubclass(exempt[name], ConsoleView), name
