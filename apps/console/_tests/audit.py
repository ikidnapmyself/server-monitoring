"""Audit entries a write path records, without the sign-ins a test client makes."""

from apps.console.models import AuditEntry


def written(**filters):
    return AuditEntry.objects.exclude(action__startswith="session.").filter(**filters)
