"""Read-only fingerprints proving additive migrations did not move legacy data."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from apps.accounts.models import Office, User  # noqa: E402
from apps.documents.models import Document, DocumentFile  # noqa: E402
from apps.tracking.models import Attachment, RecordActivity, RoutingStep, TrackingRecord  # noqa: E402


def snapshot():
    selections = {
        Office: ("id", "code", "name", "cluster", "is_active"),
        User: ("id", "role", "office_id", "is_active", "is_staff", "is_superuser"),
        TrackingRecord: ("id", "tracking_number", "subject", "originating_office_id", "current_office_id",
                         "current_holder_id", "status", "current_batch", "due_at", "first_received_at",
                         "completed_at", "approved_at", "created_at", "updated_at"),
        RoutingStep: ("id", "record_id", "sequence", "batch", "from_office_id", "to_office_id",
                      "sent_by_id", "received_by_id", "sent_at", "received_at"),
        RecordActivity: ("id", "record_id", "event", "batch", "actor_id", "message", "detail", "created_at"),
        Attachment: ("id", "record_id", "file", "checksum", "size"),
        Document: ("id", "title", "reference_number", "source", "office_id", "tracking_record_id",
                   "year", "is_active", "created_at", "updated_at", "deleted_at"),
        DocumentFile: ("id", "document_id", "file", "checksum", "size", "is_primary"),
    }
    result = {}
    for model, fields in selections.items():
        rows = list(model.objects.order_by("pk").values_list(*fields))
        payload = json.dumps(rows, default=str, ensure_ascii=True, separators=(",", ":")).encode()
        result[model._meta.label] = {"count": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = snapshot()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        changed = [key for key in result if result[key] != before.get(key)]
        if changed:
            raise SystemExit("Legacy data changed: " + ", ".join(changed))
        print("All legacy data fingerprints match; no rows moved, renumbered or changed.")
    else:
        print("Read-only baseline captured for " + str(len(result)) + " legacy tables.")


if __name__ == "__main__":
    main()
