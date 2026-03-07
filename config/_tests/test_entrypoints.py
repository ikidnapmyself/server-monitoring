"""Tests for config/asgi.py and config/wsgi.py — verify application exports."""

from django.test import SimpleTestCase


class TestAsgiEntrypoint(SimpleTestCase):
    def test_asgi_application_is_callable(self):
        from config.asgi import application

        assert callable(application)


class TestWsgiEntrypoint(SimpleTestCase):
    def test_wsgi_application_is_callable(self):
        from config.wsgi import application

        assert callable(application)
