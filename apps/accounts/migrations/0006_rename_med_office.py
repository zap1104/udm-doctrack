"""Apply MED's official office name to existing installations."""

from django.db import migrations


def rename_med(apps, schema_editor):
    Office = apps.get_model("accounts", "Office")
    Office.objects.using(schema_editor.connection.alias).filter(code="MED").update(
        name="Maintenance and Engineering Department",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_split_admin_from_system_admin"),
    ]

    # Rolling back schema changes should not restore an obsolete office label.
    operations = [migrations.RunPython(rename_med, migrations.RunPython.noop)]
