"""Migrations for the checkers app.

Migration modules start with a digit and so cannot be imported by name. The
0003 data migration has test coverage on its callable, so it gets an importable
alias here.
"""

from importlib import import_module

_0003_backfill_preflight_instance_id = import_module(
    "apps.checkers.migrations.0003_backfill_preflight_instance_id"
)
