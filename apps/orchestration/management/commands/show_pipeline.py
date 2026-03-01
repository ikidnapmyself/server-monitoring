"""
Display pipeline definitions.

Wraps PipelineInspector for CLI access.

Usage:
    manage.py show_pipeline              # list all active pipelines
    manage.py show_pipeline --all        # include inactive pipelines
    manage.py show_pipeline --name X     # specific pipeline by name
    manage.py show_pipeline --json       # JSON output
"""

import json

from django.core.management.base import BaseCommand

from apps.orchestration.services import PipelineInspector


class Command(BaseCommand):
    help = "Display pipeline definitions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            type=str,
            default=None,
            help="Show a specific pipeline by name.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="show_all",
            help="Include inactive pipelines.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output as JSON.",
        )

    def handle(self, *args, **options):
        name = options["name"]
        json_output = options["json_output"]
        show_all = options["show_all"]

        if name:
            self._show_single(name, json_output)
        else:
            self._show_list(show_all, json_output)

    def _show_single(self, name, json_output):
        detail = PipelineInspector.get_by_name(name)

        if detail is None:
            if json_output:
                self.stdout.write(json.dumps({"error": "not_found", "name": name}, indent=2))
            else:
                self.stderr.write(self.style.ERROR(f'Pipeline "{name}" not found.'))
            return

        if json_output:
            self.stdout.write(json.dumps(detail.to_dict(), indent=2))
        else:
            PipelineInspector.render_text(detail, self.stdout)

    def _show_list(self, show_all, json_output):
        details = PipelineInspector.list_all(active_only=not show_all)

        if json_output:
            self.stdout.write(json.dumps([d.to_dict() for d in details], indent=2))
            return

        if not details:
            self.stderr.write(self.style.WARNING("No pipeline definitions found."))
            return

        for detail in details:
            PipelineInspector.render_text(detail, self.stdout)
