import re
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
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

        # The 40-char raw token (secrets.token_hex(20)) is printed exactly once.
        tokens = re.findall(r"\b[0-9a-f]{40}\b", output)
        self.assertEqual(len(tokens), 1)

        # The raw token is NOT the stored SHA-256 digest.
        self.assertNotIn(key.key, output)  # never print the digest

    def test_requires_name(self):
        with self.assertRaises(CommandError):
            call_command("create_api_key")

    def test_rejects_blank_name(self):
        with self.assertRaises(CommandError):
            call_command("create_api_key", "--name", "   ")
        self.assertEqual(APIKey.objects.count(), 0)
