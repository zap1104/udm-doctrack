"""Invalidate old proofs without changing account data or historical events."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_permissionuser_userpermissionpolicy_and_more")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="security_revision",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
    ]
