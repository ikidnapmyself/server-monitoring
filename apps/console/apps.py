from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.console"

    def ready(self):
        from django.contrib.auth import signals

        from apps.console import audit

        signals.user_logged_in.connect(audit.signed_in, dispatch_uid="console.audit.sign_in")
        signals.user_logged_out.connect(audit.signed_out, dispatch_uid="console.audit.sign_out")
        signals.user_login_failed.connect(
            audit.sign_in_failed, dispatch_uid="console.audit.sign_in_failed"
        )
