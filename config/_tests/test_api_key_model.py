"""Tests for the APIKey model."""

from django.test import TestCase

from config.models import APIKey


class APIKeyModelTests(TestCase):
    def test_create_api_key(self):
        key = APIKey.objects.create(name="test-client")
        assert key.key
        assert len(key.key) == 40
        assert key.is_active is True
        assert key.name == "test-client"

    def test_key_is_unique(self):
        k1 = APIKey.objects.create(name="a")
        k2 = APIKey.objects.create(name="b")
        assert k1.key != k2.key

    def test_str_representation(self):
        key = APIKey.objects.create(name="my-service")
        assert "my-service" in str(key)
        assert "active" in str(key)

    def test_str_inactive(self):
        key = APIKey.objects.create(name="old", is_active=False)
        assert "inactive" in str(key)

    def test_generate_key_class_method(self):
        raw = APIKey.generate_key()
        assert len(raw) == 40
        assert isinstance(raw, str)

    def test_allowed_endpoints_default_empty(self):
        key = APIKey.objects.create(name="test")
        assert key.allowed_endpoints == []
