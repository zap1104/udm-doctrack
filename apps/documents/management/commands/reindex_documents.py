"""Rebuilds the search index for every document.

    python manage.py reindex_documents

Run this after changing search weights, importing data straight into the
database, or restoring a backup. It is safe to run on a live system.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.documents.models import Document
from apps.documents.services import reindex_all


class Command(BaseCommand):
    help = "Rebuild the full-text search index for all documents."

    def add_arguments(self, parser):
        parser.add_argument("--office", help="Limit to one office code, e.g. MED.")
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        queryset = Document.objects.all()
        if options["office"]:
            queryset = queryset.filter(office__code__iexact=options["office"])

        total = queryset.count()
        if not total:
            self.stdout.write("Nothing to index.")
            return

        self.stdout.write(f"Indexing {total} document(s)…")
        done = reindex_all(queryset, batch_size=options["batch_size"])
        self.stdout.write(self.style.SUCCESS(f"✓ Reindexed {done} document(s)."))
