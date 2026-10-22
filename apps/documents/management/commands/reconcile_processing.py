import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.documents.reconciliation import reconcile_processing


class Command(BaseCommand):
    help = "Inventory stale source indexes/models without OCR; --apply repairs a finite batch."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--collection", choices=["OVPA", "EXTERNAL"])
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = reconcile_processing(
                limit=options["limit"],
                after_id=options["after_id"],
                collection=options["collection"],
                dry_run=not options["apply"],
            )
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(json.dumps(result))
