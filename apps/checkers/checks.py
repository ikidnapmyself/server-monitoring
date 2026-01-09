"""
Django system checks for server-maintanence.

These checks run automatically with `python manage.py check` and can be used
to verify that the Django project is properly configured.

Available check tags:
    - database: Database connectivity check
    - migrations: Pending migrations check
    - crontab: Cron job configuration check

Usage:
    python manage.py check                     # Run all checks
    python manage.py check --tag database      # Run only database checks
    python manage.py check --tag migrations    # Run only migration checks
    python manage.py check --tag crontab       # Run only crontab checks
    python manage.py check --deploy            # Include deployment checks
"""

import subprocess

from django.core.checks import Error, Tags, Warning, register


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
                    Warning(
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
    """
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
                    Warning(
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
                    Warning(
                        f"Could not read crontab: {result.stderr.strip()}",
                        hint="Ensure cron is installed and you have permission to use it.",
                        id="checkers.W003",
                    )
                )
        else:
            crontab_content = result.stdout

            if cron_identifier not in crontab_content:
                errors.append(
                    Warning(
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
                    Warning(
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
            Warning(
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
            Warning(
                "Timeout reading crontab",
                hint="The crontab command took too long to respond.",
                id="checkers.W007",
            )
        )
    except Exception as e:
        errors.append(
            Warning(
                f"Error checking crontab: {e}",
                hint="Could not verify cron configuration.",
                id="checkers.W008",
            )
        )

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
