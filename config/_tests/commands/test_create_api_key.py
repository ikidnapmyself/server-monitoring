from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from config.models import APIKey


class CreateApiKeyCommandTests(TestCase):
    def test_mints_key_and_prints_raw_token_once(self):
        out = StringIO()
        call_command("create_api_key", "--name", "agent web-03", stdout=out)
        output = out.getvalue()

        # Exactly one key persisted, hash only (sha256 hex digest = 64 chars).
        key = APIKey.objects.get(name="agent web-03")
        self.assertEqual(len(key.key), 64)

        # The raw token is printed once and is NOT the stored hash.
        self.assertIn(key.prefix, output)
        self.assertNotIn(key.key, output)  # never print the digest

    def test_requires_name(self):
        with self.assertRaises(Exception):
            call_command("create_api_key")

    def test_rejects_blank_name(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("create_api_key", "--name", "   ")
        self.assertEqual(APIKey.objects.count(), 0)
