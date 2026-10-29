from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from apps.console.access import admits

REFUSED = "Enter the username and password of an active superuser."


class ConsoleLoginForm(AuthenticationForm):
    """Signs in active superusers only. Every refusal reads the same, so none names an account."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": REFUSED,
        "inactive": REFUSED,
    }

    def confirm_login_allowed(self, user):
        if not admits(user):
            raise ValidationError(REFUSED, code="invalid_login")
