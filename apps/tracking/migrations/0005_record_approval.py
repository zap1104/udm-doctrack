"""Record who approved a document into the repository, and when.

Self-approval is allowed — a one- or two-person office has nobody else to hand
it to, and a control that cannot be satisfied deadlocks the queue instead of
protecting anything. So the answer is to record it, not to refuse it: with
`approved_by` beside `completed_by`, "was this checked by anybody other than the
person who finished it?" becomes a query rather than a reading exercise over
timeline prose.

Both columns are null for records approved before this migration. That is
honest: nothing recorded who approved them, and back-filling `approved_by` from
`completed_by` would invent an approval that may never have been anybody's act.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracking', '0004_collapse_statuses_and_pending_upload'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='trackingrecord',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trackingrecord',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_records', to=settings.AUTH_USER_MODEL),
        ),
    ]
