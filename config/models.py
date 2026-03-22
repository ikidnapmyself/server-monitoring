"""Models for the config app (API keys, etc.)."""

import hashlib
import secrets

from django.db import models


class APIKey(models.Model):
    """API key for authenticating stateless API requests.

    The raw token is never persisted. ``key`` stores its SHA-256 hex digest
    (64 chars). ``prefix`` stores the first 8 chars of the raw token for safe
    admin display.

    ``_raw_key`` (str) — Transient instance attribute set only during initial
    ``save()`` when a key is first generated. Not persisted to the database.
    Access it immediately after ``create()``; it will be absent on any
    subsequently fetched instance.
    """

    key = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    prefix = models.CharField(max_length=8, editable=False, default="")
    name = models.CharField(max_length=100, help_text="Human-readable label for this key")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    allowed_endpoints = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional list of path prefixes this key can access. Empty = all.",
    )

    class Meta:
        app_label = "config_app"
        db_table = "config_api_key"
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"

    def __str__(self) -> str:
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    def save(self, *args, **kwargs):
        # Hash only on initial creation: once a key is stored as a SHA-256
        # digest it must never be re-hashed.  Subsequent saves (e.g. updating
        # is_active or last_used_at) leave ``key`` and ``prefix`` untouched.
        if not self.key:
            raw_key = self.generate_key()
            self._raw_key = raw_key
            self.prefix = raw_key[:8]
            self.key = hashlib.sha256(raw_key.encode()).hexdigest()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_key() -> str:
        return secrets.token_hex(20)
