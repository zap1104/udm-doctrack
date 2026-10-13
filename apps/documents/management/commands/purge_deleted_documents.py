from django.core.management.base import BaseCommand, CommandError

from apps.documents.tasks import purge_deleted_documents


class Command(BaseCommand):
    help = "Permanently delete repository documents whose 30-day recovery period has expired."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Maximum documents per cleanup run.")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("Limit must be at least 1.")
        result = purge_deleted_documents(limit=options["limit"])
        self.stdout.write(f"Permanently deleted: {result['purged']}; failed (will retry): {result['failed']}.")
        if result["failed"]:
            raise CommandError("Some repository files could not be removed. Retry cleanup after checking storage.")
