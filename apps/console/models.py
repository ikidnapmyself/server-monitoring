from django.conf import settings
from django.db import models


class AuditEntry(models.Model):
    """One console write: who did which action to what, and which fields changed.

    ``actor_name`` and ``target_name`` are captured at write time, so an entry keeps its
    names after the user or the target is deleted. Entries are never edited or deleted.
    """

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64)
    target_kind = models.CharField(max_length=64, blank=True)
    target_id = models.BigIntegerField(null=True)
    target_name = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict)
    address = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-at", "-pk"]
        indexes = [models.Index(fields=["target_kind", "target_id"])]
        verbose_name_plural = "audit entries"
