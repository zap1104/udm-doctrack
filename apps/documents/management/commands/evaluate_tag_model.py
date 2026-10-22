import json

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.documents.ml.training import evaluate_candidate


class Command(BaseCommand):
    help = "Read frozen family-disjoint evaluation; never retunes thresholds on holdout."

    def add_arguments(self, parser):
        parser.add_argument("model", type=int)
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--partition", choices=["test", "development", "calibration"], default="test")

    def handle(self, *args, **options):
        try:
            report = evaluate_candidate(
                options["model"], user=User.objects.get(pk=options["actor"]), partition=options["partition"]
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, allow_nan=False))
