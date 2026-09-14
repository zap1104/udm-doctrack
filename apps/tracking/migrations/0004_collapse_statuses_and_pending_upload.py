"""Collapse FORWARDED/RETURNED into PENDING_RECEIPT, and add the approval stage.

Two status changes land together because they touch the same column and the
same rows, and applying them apart would leave the table holding values the
field does not recognise in between.

1. FORWARDED and RETURNED are gone from `Status`. They meant exactly what
   PENDING_RECEIPT means — a document sent on with a receipt outstanding — so
   one queue was split three ways for no reader's benefit. Nothing is lost:
   `RoutingStep.action` still records FORWARD/RETURN, and the FORWARDED and
   RETURNED entries in the append-only timeline are left untouched, which is
   where the distinction was ever actually consulted.

2. COMPLETED_PENDING_UPLOAD is new, and COMPLETED changes meaning underneath
   the rows already carrying it. It used to mean "the office finished"; it now
   means "an administrator approved it into the repository". Existing rows are
   sorted by the only evidence that can tell those two apart — whether a
   Document exists for the record — so a record that was finished but never
   filed lands in the new pending-upload stage instead of silently claiming to
   be in a repository it never reached.

`max_length` goes 16 → 32 in the same operation: "COMPLETED_PENDING_UPLOAD" is
24 characters and would not fit the old column.
"""

from django.db import migrations, models

AWAITING = "PENDING_RECEIPT"
OLD_AWAITING = ("FORWARDED", "RETURNED")
COMPLETED = "COMPLETED"
PENDING_UPLOAD = "COMPLETED_PENDING_UPLOAD"

NEW_CHOICES = [
    ("DRAFT", "Draft"),
    ("PENDING_RECEIPT", "Pending receipt"),
    ("RECEIVED", "Received"),
    ("IN_PROCESS", "In process"),
    ("COMPLETED_PENDING_UPLOAD", "Completed - pending upload"),
    ("COMPLETED", "Completed"),
]

OLD_CHOICES = [
    ("DRAFT", "Draft"),
    ("PENDING_RECEIPT", "Pending receipt"),
    ("RECEIVED", "Received"),
    ("IN_PROCESS", "In process"),
    ("FORWARDED", "Forwarded"),
    ("RETURNED", "Returned"),
    ("COMPLETED", "Completed"),
]


def forwards(apps, schema_editor):
    TrackingRecord = apps.get_model("tracking", "TrackingRecord")
    Document = apps.get_model("documents", "Document")

    TrackingRecord.objects.filter(status__in=OLD_AWAITING).update(status=AWAITING)

    # "Was it actually filed?" is asked of the Document table rather than of
    # `is_archived`, because the flag and the relation are written together but
    # only the relation cannot go stale — and a record wrongly promoted here
    # would claim to be in a repository that has no copy of it.
    filed = set(
        Document.objects.filter(tracking_record__isnull=False).values_list(
            "tracking_record_id", flat=True
        )
    )
    TrackingRecord.objects.filter(status=COMPLETED).exclude(pk__in=filed).update(
        status=PENDING_UPLOAD
    )


def backwards(apps, schema_editor):
    """Undo what can be undone.

    Records waiting for approval go back to plain COMPLETED, which is what they
    were before. The FORWARDED/RETURNED split cannot be restored — those rows
    are now indistinguishable from any other PENDING_RECEIPT, which is the whole
    point of the change. Reversing therefore returns a working database with a
    coarser history than it had, not the original one.
    """
    TrackingRecord = apps.get_model("tracking", "TrackingRecord")
    TrackingRecord.objects.filter(status=PENDING_UPLOAD).update(status=COMPLETED)


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0003_rename_in_transit_to_pending_receipt"),
        # The data step reads documents.Document to tell a filed record from an
        # unfiled one.
        ("documents", "0001_initial"),
    ]

    operations = [
        # Widen the column first: the data step writes a 24-character value that
        # does not fit the old 16-character one.
        migrations.AlterField(
            model_name="trackingrecord",
            name="status",
            field=models.CharField(
                choices=OLD_CHOICES, db_index=True, default="DRAFT", max_length=32
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="trackingrecord",
            name="status",
            field=models.CharField(
                choices=NEW_CHOICES, db_index=True, default="DRAFT", max_length=32
            ),
        ),
        migrations.AlterField(
            model_name="recordactivity",
            name="event",
            field=models.CharField(
                choices=[
                    ("CREATED", "Created"),
                    ("SENT", "Routed"),
                    ("RECEIVED", "Receipt confirmed"),
                    ("REMARK", "Remark added"),
                    ("ATTACHMENT", "File attached"),
                    ("FORWARDED", "Forwarded"),
                    ("RETURNED", "Returned"),
                    ("COMPLETED", "Marked completed"),
                    ("ARCHIVED", "Archived"),
                    ("ACCESS", "Access granted"),
                    ("VIEWED", "Opened"),
                    ("PRINTED", "Routing slip printed"),
                ],
                max_length=16,
            ),
        ),
    ]
