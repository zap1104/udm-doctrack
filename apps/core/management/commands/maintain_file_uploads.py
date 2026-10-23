import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.core.file_maintenance import maintain_file_uploads


class Command(BaseCommand):
    help = "Report private-upload expiry and exact-key cleanup; --apply performs a bounded batch."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            result = maintain_file_uploads(limit=options["limit"], dry_run=not options["apply"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(("Applied: " if options["apply"] else "Dry run: ") + json.dumps(result, sort_keys=True))
