from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.documents.ml.dataset import prepare_dataset


class Command(BaseCommand):
    help = "Inventory eligible reviewed sources; --apply saves a private immutable snapshot."

    def add_arguments(self, parser):
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--office", type=int, required=True)
        parser.add_argument("--collection", choices=["OVPA", "EXTERNAL"], required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = prepare_dataset(
                user=User.objects.get(pk=options["actor"]),
                office_id=options["office"],
                collection=options["collection"],
                dry_run=not options["apply"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            str(result if isinstance(result, dict) else {"snapshot_id": result.pk, "digest": result.digest})
        )
