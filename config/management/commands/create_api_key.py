"""Mint an API key and print the raw token once.

Usage:
    python manage.py create_api_key --name "agent web-03"
"""

from django.core.management.base import BaseCommand, CommandError

from config.models import APIKey


class Command(BaseCommand):
    help = "Create an API key and print its raw token (shown once, never stored)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Human-readable label for the key.")

    def handle(self, *args, **options):
        name = options["name"].strip()
        if not name:
            raise CommandError("--name must not be empty.")

        api_key = APIKey.objects.create(name=name)
        raw = getattr(api_key, "_raw_key", "")
        if not raw:  # pragma: no cover - defensive; save() always sets it on create
            raise CommandError("Key generation failed.")

        self.stdout.write(self.style.SUCCESS(f"API key '{name}' created."))
        self.stdout.write("")
        self.stdout.write("Raw token (shown once — store it now):")
        self.stdout.write(f"    {raw}")
        self.stdout.write("")
        self.stdout.write(f"Prefix (safe to reference): {api_key.prefix}")
