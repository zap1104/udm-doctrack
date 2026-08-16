"""Rename the IN_TRANSIT status to PENDING_RECEIPT.

"In transit" described where the paper was, which is not something any office
can act on. Every record in this state is waiting on one thing — a receiving
office confirming it — so the status now says that, matching the "Pending
Receipt" queue that has always been the place these records are chased from.

The stored value is renamed too, not just the label, so the code and the screen
use one name for one thing. The data migration below is what makes that safe:
without it every existing record would keep a value no longer on the choice
list, showing as a raw "IN_TRANSIT" wherever a label was expected and matching
no filter on the tracking page.
"""

from django.db import migrations, models

OLD, NEW = "IN_TRANSIT", "PENDING_RECEIPT"


def to_pending_receipt(apps, schema_editor):
    TrackingRecord = apps.get_model("tracking", "TrackingRecord")
    TrackingRecord.objects.filter(status=OLD).update(status=NEW)


def back_to_in_transit(apps, schema_editor):
    TrackingRecord = apps.get_model("tracking", "TrackingRecord")
    TrackingRecord.objects.filter(status=NEW).update(status=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0002_trackingrecord_requested_action"),
    ]

    operations = [
        # The rows are rewritten before the choice list changes, so the table is
        # never left holding a value the field does not recognise.
        migrations.RunPython(to_pending_receipt, back_to_in_transit),
        migrations.AlterField(
            model_name="trackingrecord",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"),
                    ("PENDING_RECEIPT", "Pending receipt"),
                    ("RECEIVED", "Received"),
                    ("IN_PROCESS", "In process"),
                    ("FORWARDED", "Forwarded"),
                    ("RETURNED", "Returned"),
                    ("COMPLETED", "Completed"),
                ],
                db_index=True,
                default="DRAFT",
                max_length=16,
            ),
        ),
    ]
