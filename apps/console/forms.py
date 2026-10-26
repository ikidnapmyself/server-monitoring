from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

REFUSED = "Enter the username and password of an active staff account."


class ConsoleLoginForm(AuthenticationForm):
    """Signs in active staff only. Every refusal reads the same, so none names an account."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": REFUSED,
        "inactive": REFUSED,
    }

    def confirm_login_allowed(self, user):
        if not (user.is_active and user.is_staff):
            raise ValidationError(REFUSED, code="invalid_login")
