"""Tests for the APIKey model."""

import hashlib

from django.test import TestCase

from config.models import APIKey


class APIKeyModelTests(TestCase):
    def test_create_api_key(self):
        key = APIKey.objects.create(name="test-client")
        assert key.key
        assert len(key.key) == 64  # SHA-256 hex digest
        assert len(key.prefix) == 8
        assert key._raw_key
        assert len(key._raw_key) == 40
        assert key.is_active is True
        assert key.name == "test-client"

    def test_key_stores_hash_not_raw(self):
        key = APIKey.objects.create(name="hash-check")
        raw = key._raw_key
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        assert key.key == expected_hash

    def test_prefix_matches_raw_key(self):
        key = APIKey.objects.create(name="prefix-check")
        assert key.prefix == key._raw_key[:8]

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

    def test_raw_key_not_stored_in_db(self):
        key = APIKey.objects.create(name="db-check")
        raw = key._raw_key
        # Fetch a fresh instance from DB — _raw_key is absent because it's not a model field
        fresh = APIKey.objects.get(pk=key.pk)
        assert not hasattr(fresh, "_raw_key")
        assert fresh.key == hashlib.sha256(raw.encode()).hexdigest()
