from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0011_external_collection")]

    operations = [
        migrations.AddField(
            model_name="document", name="extraction_generation",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
    ]
