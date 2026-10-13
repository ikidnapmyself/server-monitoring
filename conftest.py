from django.conf import settings


def pytest_configure() -> None:
    # Hashed static names need a collectstatic manifest, which a test run doesn't have.
    # Settings pick the hashed backend whenever DJANGO_DEBUG is off, as a local .env may set.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
