from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    SetPasswordForm,
    UserCreationForm,
    UsernameField,
)
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


class UserCreateForm(UserCreationForm):
    """A new console user. Every one is an active superuser, since the console admits no other."""

    class Meta:
        model = get_user_model()
        fields = ("username", "email", "first_name", "last_name")
        field_classes = {"username": UsernameField}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = user.is_staff = user.is_superuser = True
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("username", "email", "first_name", "last_name", "is_active")
        labels = {"is_active": "Active"}
        help_texts = {"is_active": "An inactive account can't sign in."}

    def clean_username(self):
        username = self.cleaned_data["username"]
        others = self._meta.model.objects.exclude(pk=self.instance.pk)
        if others.filter(username__iexact=username).exists():
            raise ValidationError(
                self.instance.unique_error_message(self._meta.model, ["username"])
            )
        return username


UserPasswordForm = SetPasswordForm
