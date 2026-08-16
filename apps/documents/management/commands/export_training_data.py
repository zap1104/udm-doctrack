"""Exports reviewed metadata suggestions as JSONL for training a model later.

    python manage.py export_training_data --out training/metadata-2026-08.jsonl

Each line is one reviewed document:

    {"text": "...extracted text...",
     "suggested": {...what the rules proposed...},
     "accepted": {...what the human kept...},
     "was_edited": true}

This is the whole point of storing MetadataSuggestion: by the time the team is
ready to train a model, the archive has already produced a labelled dataset for
free, from real work rather than invented examples.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.documents.models import MetadataSuggestion


class Command(BaseCommand):
    help = "Export reviewed metadata suggestions as JSONL training data."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="training_data.jsonl", help="Output file path.")
        parser.add_argument("--min-chars", type=int, default=120,
                            help="Skip examples whose text sample is shorter than this.")
        parser.add_argument("--edited-only", action="store_true",
                            help="Export only the examples a human corrected (the most informative ones).")

    def handle(self, *args, **options):
        queryset = MetadataSuggestion.objects.filter(reviewed_at__isnull=False).select_related("document")

        path = Path(options["out"])
        path.parent.mkdir(parents=True, exist_ok=True)

        written = skipped = 0
        with path.open("w", encoding="utf-8") as handle:
            for suggestion in queryset.iterator():
                # was_edited is computed from the two JSON blobs, so it is filtered here
                # rather than in SQL.
                if options["edited_only"] and not suggestion.was_edited:
                    skipped += 1
                    continue
                sample = (suggestion.text_sample or "").strip()
                if len(sample) < options["min_chars"]:
                    skipped += 1
                    continue
                handle.write(
                    json.dumps(
                        {
                            "document_id": suggestion.document_id,
                            "engine": suggestion.engine,
                            "engine_version": suggestion.engine_version,
                            "text": sample,
                            "suggested": suggestion.suggested,
                            "accepted": suggestion.accepted,
                            "was_edited": suggestion.was_edited,
                            "reviewed_at": suggestion.reviewed_at.isoformat() if suggestion.reviewed_at else None,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1

        self.stdout.write(self.style.SUCCESS(f"OK  Wrote {written} example(s) to {path}"))
        if skipped:
            self.stdout.write(f"  Skipped {skipped} (too little text, or unedited when --edited-only was used).")
        if written < 200:
            self.stdout.write(
                "  Note: a few hundred examples is the realistic minimum before "
                "training beats the rules engine. Keep filing documents."
            )
