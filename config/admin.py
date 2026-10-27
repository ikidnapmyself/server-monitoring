"""The break-glass admin: stock Django, for active superusers only."""

from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.db.models.constants import LOOKUP_SEP


class BreakGlassLoginForm(AdminAuthenticationForm):
    """Refuses a staff account the stock way, so a right password reads like a wrong one."""

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise self.get_invalid_login_error()


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring break-glass admin"
    site_title = "Server Monitoring"
    index_title = "Raw rows. An edit here skips the console's checks."
    site_url = "/console/"
    login_form = BreakGlassLoginForm

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


class HidesSecretsAdmin(admin.ModelAdmin):
    """An excluded field can't be filtered on, or a changelist count would read it out."""

    def lookup_allowed(self, lookup, value, request=None):
        if lookup.split(LOOKUP_SEP)[0] in self.exclude:
            return False
        return super().lookup_allowed(lookup, value, request)


class APIKeyAdmin(HidesSecretsAdmin):
    """The raw token is shown once, by the console's create page, so only the console adds a key."""

    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    exclude = ["key"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.prefix}***"

    def has_add_permission(self, request):
        return False
