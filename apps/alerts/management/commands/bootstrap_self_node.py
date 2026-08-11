from django.core.management.base import BaseCommand

from apps.alerts.models import Node


class Command(BaseCommand):
    help = "Upsert the Node row representing this hub (self-node) from INSTANCE_ID."

    def handle(self, *args, **options):
        node = Node.ensure_self()
        if node is None:
            self.stdout.write(self.style.WARNING("INSTANCE_ID is not set; no self-node created."))
            return
        self.stdout.write(self.style.SUCCESS(f"Self-node ready: {node.instance_id}"))
