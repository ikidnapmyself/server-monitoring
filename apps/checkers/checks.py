"""
Django system checks for server-maintanence.

These checks run automatically with `python manage.py check` and can be used
to verify that the Django project is properly configured.

Available check tags:
    - database: Database connectivity check
    - migrations: Pending migrations check
    - crontab: Cron job configuration check
    - aliases: Shell alias configuration check (dev only)

Usage:
    python manage.py check                     # Run all checks
    python manage.py check --tag database      # Run only database checks
    python manage.py check --tag migrations    # Run only migration checks
    python manage.py check --tag crontab       # Run only crontab checks
    python manage.py check --tag aliases       # Run only aliases check
    python manage.py check --deploy            # Include deployment checks
"""

import os
import re
import subprocess
import sys
import time

from django.core.checks import Error, Info, Tags, register
from django.core.checks import Warning as CheckWarning


def _is_testing():
    """Return True if running under test framework."""
    return "pytest" in sys.modules or "test" in sys.argv


@register(Tags.database)
def check_database_connection(app_configs, **kwargs):
    """
    Check that the database connection is working.

    This verifies that Django can connect to all configured databases
    and execute a simple query.
    """
    from django.db import connections

    errors = []

    for alias in connections:
        try:
            connection = connections[alias]
            connection.ensure_connection()
            # Try a simple query to verify the connection works
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as e:
            errors.append(
                Error(
                    f"Cannot connect to database '{alias}'",
                    hint=f"Check your database configuration in settings. Error: {e}",
                    id="checkers.E001",
                )
            )

    return errors


@register("migrations")
def check_pending_migrations(app_configs, **kwargs):
    """
    Check that there are no pending migrations.

    This verifies that all migrations have been applied to the database.
    Useful for catching deployment issues where migrations weren't run.
    """
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    errors = []

    for alias in connections:
        try:
            connection = connections[alias]
            connection.prepare_database()
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())

            if plan:
                # Get list of pending migrations
                pending = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
                pending_str = ", ".join(pending[:5])  # Show first 5
                if len(pending) > 5:
                    pending_str += f" (and {len(pending) - 5} more)"

                errors.append(
                    CheckWarning(
                        f"Database '{alias}' has {len(pending)} pending migration(s)",
                        hint=f"Run 'python manage.py migrate' to apply: {pending_str}",
                        id="checkers.W001",
                    )
                )
        except Exception as e:
            errors.append(
                Error(
                    f"Cannot check migrations for database '{alias}'",
                    hint=f"Error: {e}",
                    id="checkers.E002",
                )
            )

    return errors


@register("crontab")
def check_crontab_configuration(app_configs, **kwargs):
    """
    Check that the cron job for health checks is configured.

    This verifies that the server-maintanence health check cron job
    is present in the current user's crontab.

    Only runs in production (DEBUG=False and not in tests).
    """
    from django.conf import settings

    # Skip this check in development and tests
    if settings.DEBUG or _is_testing():
        return []

    errors = []

    # Cron identifier used in setup_cron.sh
    cron_identifier = "server-maintanence"

    try:
        # Get current crontab
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            # No crontab exists for this user
            if "no crontab" in result.stderr.lower():
                errors.append(
                    CheckWarning(
                        "No crontab configured for current user",
                        hint=(
                            "Run 'bin/setup_cron.sh' to configure health check cron job. "
                            "This is optional but recommended for automated monitoring."
                        ),
                        id="checkers.W002",
                    )
                )
            else:
                errors.append(
                    CheckWarning(
                        f"Could not read crontab: {result.stderr.strip()}",
                        hint="Ensure cron is installed and you have permission to use it.",
                        id="checkers.W003",
                    )
                )
        else:
            crontab_content = result.stdout

            if cron_identifier not in crontab_content:
                errors.append(
                    CheckWarning(
                        "Health check cron job not found in crontab",
                        hint=(
                            "Run 'bin/setup_cron.sh' to configure automated health checks. "
                            "This ensures regular monitoring of server health."
                        ),
                        id="checkers.W004",
                    )
                )
            # If found, check that it references check_and_alert command
            elif "check_and_alert" not in crontab_content:
                errors.append(
                    CheckWarning(
                        "Cron job found but may not be running health checks with alerts",
                        hint=(
                            "The crontab contains 'server-maintanence' but not 'check_and_alert'. "
                            "Verify the cron job is correctly configured to create alerts."
                        ),
                        id="checkers.W005",
                    )
                )
    except FileNotFoundError:
        errors.append(
            CheckWarning(
                "crontab command not found",
                hint=(
                    "Cron may not be installed on this system. "
                    "Install cron or use an alternative scheduler."
                ),
                id="checkers.W006",
            )
        )
    except subprocess.TimeoutExpired:
        errors.append(
            CheckWarning(
                "Timeout reading crontab",
                hint="The crontab command took too long to respond.",
                id="checkers.W007",
            )
        )
    except Exception as e:
        errors.append(
            CheckWarning(
                f"Error checking crontab: {e}",
                hint="Could not verify cron configuration.",
                id="checkers.W008",
            )
        )

    return errors


def _aliases_file_exists():
    """Return True if bin/aliases.sh exists in the project root."""
    import django.conf

    base_dir = getattr(django.conf.settings, "BASE_DIR", None)
    if base_dir is None:
        return True  # Can't check, don't warn
    aliases_path = os.path.join(str(base_dir), "bin", "aliases.sh")
    return os.path.isfile(aliases_path)


@register("aliases")
def check_aliases_configured(app_configs, **kwargs):
    """
    Check that shell aliases are configured for management commands.

    Only runs in development (DEBUG=True and not in tests).
    """
    from django.conf import settings

    if not settings.DEBUG or _is_testing():
        return []

    errors = []

    if not _aliases_file_exists():
        errors.append(
            CheckWarning(
                "Shell aliases not configured for management commands",
                hint=(
                    "Run 'bin/setup_aliases.sh' to set up quick aliases like "
                    "sm-check-health, sm-run-check, etc. "
                    "This is optional but improves developer experience."
                ),
                id="checkers.W009",
            )
        )

    return errors


@register("security")
def check_debug_mode(app_configs, **kwargs):
    """Check that DEBUG is not enabled in production."""
    from django.conf import settings

    if _is_testing():
        return []
    errors = []
    if settings.DEBUG:
        errors.append(
            CheckWarning(
                "DEBUG mode is enabled",
                hint="Set DEBUG=False in production. DEBUG=True exposes sensitive information.",
                id="checkers.W010",
            )
        )
    return errors


@register("security")
def check_secret_key_strength(app_configs, **kwargs):
    """Check that SECRET_KEY is sufficiently strong."""
    from django.conf import settings

    if _is_testing():
        return []
    errors = []
    secret_key = getattr(settings, "SECRET_KEY", "")
    if len(secret_key) < 50 or "insecure" in secret_key.lower():
        errors.append(
            CheckWarning(
                f"SECRET_KEY appears weak ({len(secret_key)} chars)",
                hint=(
                    "Generate a strong secret key: "
                    'python -c "from django.core.management.utils import get_random_secret_key;'
                    ' print(get_random_secret_key())"'
                ),
                id="checkers.W011",
            )
        )
    return errors


@register("environment")
def check_env_file_exists(app_configs, **kwargs):
    """Check that .env file exists."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.isfile(env_path):
        errors.append(
            CheckWarning(
                ".env file not found",
                hint="Copy .env.sample to .env and configure: cp .env.sample .env",
                id="checkers.W012",
            )
        )
    return errors


@register("environment")
def check_required_env_vars(app_configs, **kwargs):
    """Check that required env vars from .env.sample are set."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    sample_path = os.path.join(base_dir, ".env.sample")
    if not os.path.isfile(sample_path):
        return errors
    try:
        with open(sample_path) as f:
            content = f.read()
    except OSError:
        return errors

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", stripped)
        if match:
            var_name = match.group(1)
            if var_name not in os.environ:
                errors.append(
                    Info(
                        f"Environment variable {var_name} not set (defined in .env.sample)",
                        hint=f"Set {var_name} in your .env file or shell environment.",
                        id="checkers.I003",
                    )
                )
    return errors


@register("environment")
def check_base_dir_writable(app_configs, **kwargs):
    """Check that the project directory is writable."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    if base_dir and not os.access(base_dir, os.W_OK):
        errors.append(
            CheckWarning(
                "Project directory is not writable",
                hint="Cron logs and other output require write access to the project directory.",
                id="checkers.W017",
            )
        )
    return errors


@register("pipeline")
def check_pipeline_status(app_configs, **kwargs):
    """Report pipeline definition counts (active/inactive)."""
    from apps.orchestration.models import PipelineDefinition

    errors = []
    try:
        definitions = list(PipelineDefinition.objects.all().values("name", "is_active"))
        total = len(definitions)
        active = sum(1 for d in definitions if d["is_active"])
        inactive = total - active
        names = ", ".join(
            f"{d['name']} ({'active' if d['is_active'] else 'inactive'})" for d in definitions
        )
        errors.append(
            Info(
                f"{total} pipeline definition(s) ({active} active, {inactive} inactive)"
                + (f": {names}" if names else ""),
                id="checkers.I001",
            )
        )
    except Exception as e:
        errors.append(CheckWarning(f"Cannot check pipeline definitions: {e}", id="checkers.I001"))
    return errors


@register("pipeline")
def check_notification_channels(app_configs, **kwargs):
    """Check notification channel health."""
    from apps.notify.models import NotificationChannel

    errors = []
    try:
        active_channels = list(
            NotificationChannel.objects.filter(is_active=True).values("name", "driver", "config")
        )
        if not active_channels:
            errors.append(
                CheckWarning(
                    "No active notification channels configured",
                    hint=(
                        "Create notification channels via Django Admin"
                        " or run 'python manage.py setup_instance'."
                    ),
                    id="checkers.W014",
                )
            )
        else:
            for ch in active_channels:
                if not ch["config"]:
                    errors.append(
                        CheckWarning(
                            f"Notification channel '{ch['name']}' ({ch['driver']})"
                            f" has empty config",
                            hint=(
                                f"Configure {ch['driver']} settings for channel"
                                f" '{ch['name']}' in Django Admin."
                            ),
                            id="checkers.W014",
                        )
                    )
    except Exception as e:
        errors.append(CheckWarning(f"Cannot check notification channels: {e}", id="checkers.W014"))
    return errors


@register("crontab")
def check_cron_log_freshness(app_configs, **kwargs):
    """Check that cron.log has been updated recently."""
    from django.conf import settings

    if _is_testing():
        return []
    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    log_path = os.path.join(base_dir, "cron.log")
    if not os.path.isfile(log_path):
        return errors
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or "server-maintanence" not in result.stdout:
            return errors
    except Exception:
        return errors
    try:
        mtime = os.path.getmtime(log_path)
        age_seconds = time.time() - mtime
        if age_seconds > 3600:
            age_minutes = int(age_seconds / 60)
            errors.append(
                CheckWarning(
                    f"cron.log last updated {age_minutes} minutes ago",
                    hint=(
                        "The cron log hasn't been updated in over an hour."
                        " Check that the cron job is running: crontab -l"
                    ),
                    id="checkers.W015",
                )
            )
    except OSError:
        pass
    return errors


@register("crontab")
def check_cron_log_size(app_configs, **kwargs):
    """Check that cron.log is not too large."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    log_path = os.path.join(base_dir, "cron.log")
    if not os.path.isfile(log_path):
        return errors
    try:
        size_bytes = os.path.getsize(log_path)
        max_size = 50 * 1024 * 1024
        if size_bytes > max_size:
            size_mb = size_bytes / (1024 * 1024)
            errors.append(
                CheckWarning(
                    f"cron.log is {size_mb:.0f}MB (threshold: 50MB)",
                    hint="Consider log rotation: logrotate, or truncate with: > cron.log",
                    id="checkers.W016",
                )
            )
    except OSError:
        pass
    return errors


@register(Tags.database, deploy=True)
def check_database_tables_exist(app_configs, **kwargs):
    """
    Check that expected database tables exist.

    This is a deployment check that verifies migrations have created
    the expected tables. Only runs with --deploy flag.
    """
    from django.db import connection

    errors = []

    try:
        # Get all tables in the database
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)

        # Check that django_migrations table exists (basic sanity check)
        if "django_migrations" not in tables:
            errors.append(
                Error(
                    "django_migrations table not found",
                    hint=(
                        "The database may not be initialized. "
                        "Run 'python manage.py migrate' to create tables."
                    ),
                    id="checkers.E003",
                )
            )
    except Exception as e:
        errors.append(
            Error(
                f"Cannot inspect database tables: {e}",
                hint="Ensure database is accessible and properly configured.",
                id="checkers.E004",
            )
        )

    return errors
