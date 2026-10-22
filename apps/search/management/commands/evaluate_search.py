import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.search.evaluation import evaluate_search_judgments


class Command(BaseCommand):
    help = "Evaluate supplied authorized judgments without logging queries or claiming production quality."

    def add_arguments(self, parser):
        parser.add_argument("judgments")
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--collection", choices=["OVPA", "EXTERNAL"], default="OVPA")

    def handle(self, *args, **options):
        try:
            path = Path(options["judgments"])
            if path.stat().st_size > 1_000_000:
                raise ValueError("The judgment file exceeds 1 MB.")
            judgments = json.loads(path.read_text(encoding="utf-8"))
            report = evaluate_search_judgments(
                User.objects.get(pk=options["actor"]), judgments, collection=options["collection"]
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, allow_nan=False))
