"""Recover committed processing intents, preserving the original authority."""
from django.core.management.base import BaseCommand

from apps.documents.processing import recover_processing


class Command(BaseCommand):
    help = "Inventory recoverable source-unit jobs; --apply requeues eligible original intents."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        result = recover_processing(limit=options["limit"], dry_run=not options["apply"])
        self.stdout.write(f"{'Dry run' if result['dry_run'] else 'Recovered'}: {result['count']} eligible processing intent(s).")
