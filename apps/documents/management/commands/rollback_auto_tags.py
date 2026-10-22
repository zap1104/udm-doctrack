from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.documents.automatic_tags import rollback_auto_tags


class Command(BaseCommand):
    help = "Inventory model contributions; --apply removes automatic contributions and preserves human tags."

    def add_arguments(self, parser):
        parser.add_argument("model", type=int)
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = rollback_auto_tags(
                user=User.objects.get(pk=options["actor"]), model_id=options["model"], dry_run=not options["apply"]
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(result))
