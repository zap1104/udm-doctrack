"""Turns on the PostgreSQL extensions the search engine relies on.

Run once per database, after `migrate`:

    python manage.py init_db

Needs a database user with rights to create extensions. On a managed host
(Azure, Render, Supabase) the default owner usually has them; if not, ask the
DBA to run the two CREATE EXTENSION lines printed by --dry-run.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

EXTENSIONS = [
    ("pg_trgm", "fuzzy matching so 'memorandom' still finds 'memorandum'"),
    ("unaccent", "ignores accents, e.g. 'Peñafrancia' matches 'Penafrancia'"),
]


class Command(BaseCommand):
    help = "Enable pg_trgm and unaccent for search."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the SQL instead of running it (hand this to your DBA).",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(
                self.style.WARNING(
                    f"Database engine is '{connection.vendor}', not PostgreSQL. "
                    "Search will fall back to plain matching; ranking still works."
                )
            )
            return

        for name, why in EXTENSIONS:
            sql = f'CREATE EXTENSION IF NOT EXISTS "{name}";'
            if options["dry_run"]:
                self.stdout.write(f"{sql}  -- {why}")
                continue
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                self.stdout.write(self.style.SUCCESS(f"✓ {name} ready — {why}"))
            except Exception as exc:  # pragma: no cover - depends on DB privileges
                self.stderr.write(
                    self.style.WARNING(
                        f"× Could not enable {name}: {exc}\n"
                        f"  The app still runs. Ask your database admin to run: {sql}"
                    )
                )

        if not options["dry_run"]:
            self.stdout.write("")
            self.stdout.write("Next: python manage.py seed_demo  (creates offices, users and sample records)")
