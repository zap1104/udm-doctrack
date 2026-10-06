"""Update MED's office label without replacing the office or its references."""

from django.db import migrations


def rename_med(apps, schema_editor):
    Office = apps.get_model("accounts", "Office")
    Office.objects.using(schema_editor.connection.alias).filter(code="MED").update(
        name="Maintenance and Engineering Division",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_user_verified_email"),
    ]

    # Reversing schema changes should not reinstate an outdated office name.
    operations = [migrations.RunPython(rename_med, migrations.RunPython.noop)]
