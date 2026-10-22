from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.documents.ml.lifecycle import activate_model


class Command(BaseCommand):
    help = "Explicitly activate a valid scoped topic model; no automatic promotion."

    def add_arguments(self, parser):
        parser.add_argument("model", type=int)
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--mode", choices=["shadow", "suggestion", "automatic"], default="shadow")

    def handle(self, *args, **options):
        try:
            model = activate_model(options["model"], user=User.objects.get(pk=options["actor"]), mode=options["mode"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Model {model.pk} active in {model.mode} mode.")
        self.stdout.write(
            model.evaluation.get("holdout", {}).get(
                "declaration", "Holdout reuse was not tracked for this older candidate."
            )
        )
