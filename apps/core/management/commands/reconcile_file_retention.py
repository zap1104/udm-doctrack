import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.core.file_maintenance import bind_legacy_identity, reconcile_file_retention


class Command(BaseCommand):
    help = "Report groups exceeding five available versions; --apply retires a bounded batch."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--bind-legacy-kind", choices=["tracking", "external", "repository"])
        parser.add_argument("--bind-legacy-id", type=int)

    def handle(self, *args, **options):
        try:
            if bool(options["bind_legacy_kind"]) != bool(options["bind_legacy_id"]):
                raise ValidationError("Supply both the legacy kind and exact version ID.")
            result = bind_legacy_identity(options["bind_legacy_kind"], options["bind_legacy_id"], dry_run=not options["apply"]) if options["bind_legacy_kind"] else reconcile_file_retention(limit=options["limit"], dry_run=not options["apply"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(("Applied: " if options["apply"] else "Dry run: ") + json.dumps(result, sort_keys=True))
