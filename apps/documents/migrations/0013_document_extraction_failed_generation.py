from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0012_document_extraction_generation")]

    operations = [
        migrations.AddField(
            model_name="document", name="extraction_failed_generation",
            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True),
        ),
    ]
