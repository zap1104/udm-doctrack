from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.documents.ml.training import train_candidate


class Command(BaseCommand):
    help = "Train a private, inactive CPU topic-model candidate from an eligible snapshot."

    def add_arguments(self, parser):
        parser.add_argument("snapshot", type=int)
        parser.add_argument("--actor", type=int, required=True)

    def handle(self, *args, **options):
        try:
            model = train_candidate(options["snapshot"], user=User.objects.get(pk=options["actor"]))
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Candidate {model.pk}: {model.artifact_digest}. Explicit activation is required.")
