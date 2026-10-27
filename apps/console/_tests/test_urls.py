from apps.console.urls import urlpatterns
from apps.console.views.base import ConsoleView


def test_every_route_is_a_console_view_on_its_one_dispatch():
    views = [pattern.callback.view_class for pattern in urlpatterns]

    assert views
    assert [view.__name__ for view in views if not issubclass(view, ConsoleView)] == []
    assert [
        klass.__name__
        for view in views
        for klass in view.__mro__[: view.__mro__.index(ConsoleView)]
        if "dispatch" in vars(klass)
    ] == []
